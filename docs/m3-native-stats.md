# M3 第一增量：vLLM 原生统计采集

沿用唯一路线 `implementation-plan.md`。本增量接入 vLLM 0.29.0 已有的 StatLogger 回调，提供逐请求缓存统计与服务级调度快照。M3 的完整 KV 生命周期及逐请求 Scheduler 事件仍待后续实现。

## 已核对的数据来源

- `vllm/v1/metrics/stats.py::FinishedRequestStats`：外部 request ID、输入/输出 token 数、cached token 数。`output_processor.py::_update_stats_from_finished` 使用 `external_req_id`，与 OTel `llm_request` 使用的 ID 同源。
- `SchedulerStats`：运行请求、普通等待队列、skipped-waiting 队列、KV usage 都是 engine 级快照。`PrefixCacheStats` 给出调度区间内的 requests/queries/hits，并把曾被抢占的请求统计另列；跳过本地 cache lookup 的请求不计入这组数据。它没有 request ID。`connector_prefix_cache_stats` 是独立的连接器聚合数据。
- `SchedulerStats.kv_cache_eviction_events`：可提供采样的 block lifetime、淘汰前 idle 时长和 reuse gap。只有启用 vLLM `kv_cache_metrics` 才会收集；这些样本没有 block ID 或 request ID，因此不能重建完整分配/复用/释放图。
- `iteration_details`（若 runtime 提供）记录 engine iteration 的 context/generation 数量与耗时。`IterationStats.num_preempted_reqs` 以及 `EngineCoreOutput` 中的逐请求 QUEUED/SCHEDULED/PREEMPTED 事件在 StatLogger 回调边界已经汇总为计数；该回调没有逐请求事件列表。
- `distributed/kv_events.py` 的 BlockStored/BlockRemoved 用内容 hash 标识缓存数据，没有 request ID；不能直接套用 InferScope 的物理 block 分配/引用/释放分析器。

来源：[v0.29.0 stats.py](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/metrics/stats.py)、[logger 接口](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/metrics/loggers.py)、[EngineArgs](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/engine/arg_utils.py)、[AsyncLLM](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/engine/async_llm.py)、[KV 事件](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/distributed/kv_events.py)。已与远端安装源码核对。统计结构不属于 vLLM 稳定 API，因此采集器限制精确版本。

## 实现决定

1. 可选 `make_stat_logger(output_directory)` 工厂接入 `AsyncLLM.from_engine_args(..., stat_loggers=[factory])`，不修改 vLLM，不新增产品运行依赖。若要收集 KV 淘汰样本，还需在 vLLM 配置中启用 `kv_cache_metrics` 并设置采样率。
2. 每个进程/engine 独立 JSONL 文件，独占创建；只保存字段白名单，不保存 prompt、token IDs、生成文本、模型路径或 LoRA 信息。
3. `observed_at_ns` 是采集回调的墙钟时间，不是精确调度时间。请求缓存量在完成时报告，不能视为 prefix lookup 发生时间，也不能区分本地缓存与外部 KV 传输。
4. `runtime-stats` CLI 离线解析并报告请求与 engine 两类数据；服务级数据不转换成带伪造 request ID 的统一事件。`summary --runtime-stats` 可在离线阶段把两个来源按 request ID 关联，但不会合成事件或修改 OTel 原始 trace。
5. 严格校验格式、非负计数、缓存量上界、有限利用率、版本及缓存样本；错误标出文件与行号。重复请求观测标为歧义，缓存量保持 UNKNOWN。缺少迭代或调度数据保持 UNKNOWN，零值与未知值分开。

## 使用方法

在 vLLM 0.29.0 环境中，创建 logger 工厂并传给 `AsyncLLM.from_engine_args`：

```python
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.v1.engine.async_llm import AsyncLLM

from inferscope.runtime.vllm_stats import make_stat_logger

engine_args = AsyncEngineArgs(model="/path/to/model")
logger_factory = make_stat_logger("/tmp/inferscope-stats")
# 需要 KV block 淘汰样本时启用可选 vLLM 采样
engine_args.kv_cache_metrics = True
engine_args.kv_cache_metrics_sample = 1.0
engine = AsyncLLM.from_engine_args(engine_args, stat_loggers=[logger_factory])
```

每个 engine 写一个独占创建的 `vllm-stats-<pid>-<engine>.jsonl`。结束采集后可离线检查：

```bash
inferscope runtime-stats /tmp/inferscope-stats/vllm-stats-<pid>-0.jsonl
inferscope runtime-stats /tmp/inferscope-stats/vllm-stats-<pid>-0.jsonl --json
```

在装有 vLLM 的开发机上，可用 `scripts/vllm_stats_smoke.py` 对现有模型执行两次固定重复前缀请求：

```bash
PYTHONPATH=src VLLM_USE_FLASHINFER_SAMPLER=0 \
  python scripts/vllm_stats_smoke.py --model /path/to/model --output-directory /tmp/inferscope-stats
```

采集器和离线 CLI 本身没有 vLLM 运行依赖；只有启用原生回调时才需要安装经验证的 vLLM 版本。Scheduler 把普通等待和 skipped-waiting 分开输出。KV metrics 采样率为 1.0 会采集每个淘汰样本，生产环境可按成本调整。

## 与 OTel 请求摘要关联

如果两份数据来自同一批请求，且保留相同的 external request ID，可在 summary 中关联一个或多个 engine 文件：

```bash
inferscope adapt vllm /tmp/raw-otel.json --output /tmp/trace.jsonl
inferscope summary /tmp/trace.jsonl \
  --runtime-stats /tmp/engine-0.jsonl \
  --runtime-stats /tmp/engine-1.jsonl --json
```

仅当 trace 与原生统计两侧的 ID 唯一、原生观测唯一且 cached tokens 来源为 `OBSERVED` 时，逐请求结果才会合并。无匹配、重复回调、跨 engine 的 ID 冲突及原生值未知均标记 `UNKNOWN`，并在 `runtime_stats.correlation` 中分别计数；native 原始请求报告和 engine 报告仍保留在 JSON 中。逐请求的 `observed_at_ns` 是 StatLogger 回调采集墙钟时间，不是调度或 cache lookup 的精确时刻。

Scheduler、Prefix Cache 聚合和 KV 淘汰样本仍为 `scope=ENGINE`，在文本/JSON 报告中独立呈现，不会复制到某个请求上。当前已保存的 M2 OTel fixture 与 M3 原生 fixture 是不同 smoke 运行，request ID 不相同；它们分别证明各自采集/解析链路，不能冒充一次真实的跨来源关联验收。M4 需要用同一 Agent 运行实际核对这一点。

## 真实采集验收

在远端现有 Llama-3-8B-Instruct 权重与 vLLM 0.29.0 环境，脚本实际生成 20 条回调记录。两条顺序请求各输入 360、输出 8 tokens：`req-m3-cold` 缓存 0，`req-m3-warm` 缓存 352。逐请求缓存值与 vLLM 返回对象相等；同一轮 PrefixCacheStats 汇总记录 720 个 query tokens、352 个 hit tokens，另有 20 条 engine 级 Scheduler 快照。KV metrics 以 100% 采样启用，并实际采到 1 条淘汰样本（生命周期约 0.369 秒，淘汰前 idle 约 0.369 秒，无 reuse gap）。原始白名单记录保存在 `tests/fixtures/vllm-0.29.0-native-stats.jsonl`，不含 prompt、token IDs 或生成内容。

这是受控的接口及缓存观测验证。采样淘汰统计具备 block 生命周期的一部分信息，但没有 block 身份；逐请求 Scheduler/抢占归因仍不可用。summary 联表只按原生 request ID 做精确关联；这些不可观测字段仍保持 `UNKNOWN`，不对 vLLM 内部做侵入式修改。
