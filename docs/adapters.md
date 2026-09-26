# Framework Adapter 使用说明

当前 Adapter 离线读取 OpenTelemetry Collector 导出的 JSON，不连接或修改正在运行的 Serving Server。转换结果是 InferScope v1 Trace JSONL，可继续交给 `summary`、`inspect` 和 `trace` 分析。

```bash
inferscope adapt vllm /path/to/otel-trace.json --output trace.jsonl
inferscope adapt sglang /path/to/otel-trace.json --output trace.jsonl
inferscope summary trace.jsonl
```

可用 `examples/demo-otel.json` 试跑 vLLM adapter。

输入支持 OTLP JSON 中的 `resourceSpans[].scopeSpans[].spans[]`，也支持顶层 `spans` 数组。请求关联字段识别 `gen_ai.request.id`、`request_id` 和 `req_id`。SGLang 子 span 若没有直接 request ID，仅当其 `traceId` 唯一对应一个带 request ID 的根 span 时才继承关联；没有唯一根 span 时保留为未关联数据，不猜测归属。仍无法关联或缺少有效纳秒时间戳的 span 会被跳过。

## vLLM

vLLM 0.29.0 可通过 OTLP/gRPC 导出 OpenTelemetry trace。本项目附有仅用于测试的临时接收器；它只绑定 loopback，累加多个 Export 批次，并在退出时写 JSON。需使用独立临时目录和未占用端口，不要覆盖已有 trace 或服务：

```bash
TMP_DIR="$(mktemp -d /tmp/inferscope-vllm.XXXXXX)"
# 终端 A：保持运行，完成采集后用 Ctrl-C 正常停止
python scripts/vllm_otel_receiver.py --port 4317 --output "$TMP_DIR/raw-otel.json" --timeout-seconds 900
```

在另一终端使用已有模型和隔离的 loopback 服务端口；`grpc://` scheme 及 `OTEL_EXPORTER_OTLP_TRACES_INSECURE=true` 与 [vLLM 0.29.0 官方示例](https://github.com/vllm-project/vllm/blob/v0.29.0/examples/observability/opentelemetry/README.md)一致：

```bash
export OTEL_EXPORTER_OTLP_TRACES_INSECURE=true
vllm serve /path/to/existing/model \
  --host 127.0.0.1 --port 18000 \
  --max-model-len 4096 --max-num-seqs 1 --gpu-memory-utilization 0.85 \
  --otlp-traces-endpoint grpc://127.0.0.1:4317
```

服务就绪后，先从只读 `GET /v1/models` 响应取得 model id，再只发送一次固定 smoke 请求；不要用它做压测或 Agent 评测：

```bash
curl -sS http://127.0.0.1:18000/v1/models
curl -sS http://127.0.0.1:18000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<上一步返回的 model id>","prompt":"Please respond with exactly: INFER_SCOPE_SMOKE_OK.","max_tokens":8,"temperature":0}'
```

收到响应后优雅停止本次 vLLM 进程，并等待 trace 导出，再停止接收器。以原始 `llm_request` 中 `gen_ai.request.id` 的值检查同一请求：

```bash
inferscope adapt vllm "$TMP_DIR/raw-otel.json" --output "$TMP_DIR/trace.jsonl"
inferscope summary "$TMP_DIR/trace.jsonl" --json
REQUEST_ID="$(python -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); s=next(s for r in d["resourceSpans"] for g in r.get("scopeSpans", []) for s in g.get("spans", []) if s.get("name") == "llm_request"); a={x["key"]:next(iter(x["value"].values())) for x in s.get("attributes", [])}; print(a["gen_ai.request.id"])' "$TMP_DIR/raw-otel.json")"
inferscope inspect "$TMP_DIR/trace.jsonl" "$REQUEST_ID" --json
```

仓库中的 `tests/fixtures/vllm-0.29.0-otel.json` 是这条真实请求的白名单脱敏样本；原始 trace、prompt、主机路径、原始 request/span id 和资源属性不入库。可运行 `pytest tests/test_vllm_live_fixture.py tests/test_adapters.py tests/test_analyzer.py -q` 回归 fixture、Adapter 和 CLI。

本次已验证：一条合成请求生成唯一 `llm_request`，span 中实际提供了 queue、prefill、decode、TTFT、E2E 五项 latency 和 prompt/completion token 数；它们都映射为 `OBSERVED`。若 latency 缺失，未观测阶段保持 `UNKNOWN`，仅生命周期边界可支持的 E2E 标为 `DERIVED`。基础 trace 未启用 `--collect-detailed-traces`；本次 trace 不能证明 KV、Prefix Cache 或 Scheduler 行为，这些仍为未知。其它非请求 span 以 `FRAMEWORK_SPAN` 保留名称和时长，不推断其业务含义。

在 CUDA 12.0 与当前 FlashInfer 采样内核不兼容的环境，可按 vLLM 0.29.0 自带回退选项设置 `VLLM_USE_FLASHINFER_SAMPLER=0`；这不是 InferScope 依赖，也不改变 trace 含义。若虚拟环境的子进程找不到 `ninja`，将该环境的 `bin` 目录加入 `PATH`。其它运行环境无需照搬这两项机器特定设置。

## SGLang

已对 SGLang 0.5.20 做真实 OTel smoke test：既有 Llama-3-8B-Instruct 服务通过 loopback OTLP 收到一条合成请求的 trace。白名单脱敏样本保存在 `tests/fixtures/sglang-0.5.20-otel.json`，对应的 Adapter、分析器和 CLI 回归测试为 `tests/test_sglang_live_fixture.py`。

实测根 span 带 `gen_ai.request.id`，同一 `traceId` 下的 `tokenize`、`request_process`、`prefill_waiting`、`prefill_forward`、`decode_forward`、Scheduler 等子 span 没有 request ID。Adapter 现在只在该 trace 唯一对应一个请求根 span 时关联这些子 span，并把 `request_id_source=TRACE_ID_ASSOCIATION` 写入统一事件；span 自带 ID 时优先使用直接属性。trace 中有多个候选请求根时不关联子 span，避免错配。

Adapter 映射根 span 提供的 Queue、Prefill、Decode、TTFT、E2E latency 秒值为纳秒制 `OBSERVED` 指标，并映射其直接提供的 `gen_ai.usage.cached_tokens`。`prefill_waiting` 的起止时间用于 queued/scheduled 边界；若没有根 span Queue 指标，Queue 从该边界推导，而不把入口和 tokenize 时间计入等待。已识别 `prefill_forward`、`chunked_prefill` 和 `decode_forward`；其它 stage 继续以 `FRAMEWORK_SPAN` 保留，不猜测阶段含义。缺少的 latency/cache 数据仍显示 `UNKNOWN`。

可对保存的样本端到端复核：

```bash
python -m inferscope adapt sglang tests/fixtures/sglang-0.5.20-otel.json --output /tmp/sglang-trace.jsonl
python -m inferscope summary /tmp/sglang-trace.jsonl --json
python -m inferscope inspect /tmp/sglang-trace.jsonl req-sglang-001 --json
```

此 smoke test 证明的是单条合成请求的 trace/Adapter/CLI 字段映射，不等同于 Aider 端到端验收，也不证明 SGLang 提供完整的 Prefix Cache、KV block 生命周期或逐请求 Scheduler 行为；这些缺口仍按 `UNKNOWN` 处理。完整的 SGLang+Aider 对照验收仍在进行。

## 已知限制

- OTel spans 不一定携带 Prefix Cache token 级命中、KV block 分配/释放/淘汰、Scheduler batch 等数据；缺失字段必须由显式 trace 事件补充。
- Adapter 不反序列化 SGLang 的 pickle request dump。该数据用于 request replay，不是统一的低层 runtime trace。
- 导出的时间戳必须是 Unix 纳秒；Duration 属性必须是秒。没有这些字段就不合成阶段时间。

框架选项和观测字段会随版本变化。当前映射围绕 vLLM 0.29.0 与 SGLang 0.5.20 的已安装环境及其 OpenTelemetry/trace schema；引入其他版本时需更新示例和兼容性检查。
