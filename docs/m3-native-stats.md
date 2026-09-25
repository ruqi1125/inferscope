# M3 第一增量：vLLM 原生统计采集

沿用唯一路线 `implementation-plan.md`。本增量接入 vLLM 0.29.0 已有的 StatLogger 回调，提供逐请求缓存统计与服务级调度快照。M3 的完整 KV 生命周期及逐请求 Scheduler 事件仍待后续实现。

## 已核对的数据来源

- `vllm/v1/metrics/stats.py::FinishedRequestStats`：外部 request ID、输入/输出 token 数、cached token 数。`output_processor.py::_update_stats_from_finished` 使用 `external_req_id`，与 OTel `llm_request` 使用的 ID 同源。
- `SchedulerStats`：运行/等待请求数、KV cache usage；均为 engine 级快照。
- `IterationStats.num_preempted_reqs`：本次迭代的服务级抢占数，没有逐请求关联。
- `distributed/kv_events.py` 的 BlockStored/BlockRemoved 用内容 hash 标识缓存数据，没有 request ID；不能直接套用 InferScope 的物理 block 分配/引用/释放分析器。

来源：[v0.29.0 stats.py](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/metrics/stats.py)、[logger 接口](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/metrics/loggers.py)、[KV 事件](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/distributed/kv_events.py)。已与远端安装源码核对。统计结构不属于 vLLM 稳定 API，因此采集器限制精确版本。

## 实现决定

1. 可选 `make_stat_logger(output_directory)` 工厂接入 `AsyncLLM.from_engine_args(..., stat_loggers=[factory])`，不修改 vLLM，不新增产品运行依赖。
2. 每个进程/engine 独立 JSONL 文件，独占创建；只保存字段白名单，不保存 prompt、token IDs、生成文本、模型路径或 LoRA 信息。
3. `observed_at_ns` 是采集回调的墙钟时间，不是精确调度时间。请求缓存量在完成时报告，不能视为 prefix lookup 发生时间，也不能区分本地缓存与外部 KV 传输。
4. `runtime-stats` CLI 离线解析并报告请求与 engine 两类数据；服务级数据不转换成带伪造 request ID 的统一事件。此增量不改变现有 OTel 事件链路。
5. 严格校验格式、非负计数、缓存量上界、有限利用率、版本；错误标出文件与行号。重复请求观测标为歧义，缓存量保持 UNKNOWN。缺少迭代或调度数据保持 UNKNOWN，零值与未知值分开。

## 使用方法

在 vLLM 0.29.0 环境中，创建 logger 工厂并传给 `AsyncLLM.from_engine_args`：

```python
from inferscope.runtime.vllm_stats import make_stat_logger
from vllm.v1.engine.async_llm import AsyncLLM

logger_factory = make_stat_logger("/tmp/inferscope-stats")
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

采集器和离线 CLI 本身没有 vLLM 运行依赖；只有启用原生回调时才需要安装经验证的 vLLM 版本。Scheduler 的等待计数对应普通等待队列，不包含另行统计的 skipped-waiting 队列。

## 真实采集验收

在远端现有 Llama-3-8B-Instruct 权重与 vLLM 0.29.0 环境，脚本实际生成 20 条回调记录。两条顺序请求各输入 360、输出 8 tokens：`req-m3-cold` 缓存 0，`req-m3-warm` 缓存 352。采集值与 vLLM 返回对象逐项相等，且有 20 条 engine 级 scheduler 快照。原始白名单记录保存在 `tests/fixtures/vllm-0.29.0-native-stats.jsonl`，不含 prompt、token IDs 或生成内容。

这是受控的接口及缓存观测验证；它没有证明 cache block 的分配/释放/驱逐生命周期，也没有提供逐请求的调度或抢占归因。因此 M3 继续进行，后续按总体路线核验这些字段可否从 vLLM 原生来源取得；不足时先记录具体缺口与影响。
