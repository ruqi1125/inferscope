# Framework Adapter 使用说明

当前 Adapter 离线读取 OpenTelemetry Collector 导出的 JSON，不连接或修改正在运行的 Serving Server。转换结果是 InferScope v1 Trace JSONL，可继续交给 `summary`、`inspect` 和 `trace` 分析。

```bash
inferscope adapt vllm /path/to/otel-trace.json --output trace.jsonl
inferscope adapt sglang /path/to/otel-trace.json --output trace.jsonl
inferscope summary trace.jsonl
```

可用 `examples/demo-otel.json` 试跑 vLLM adapter。

输入支持 OTLP JSON 中的 `resourceSpans[].scopeSpans[].spans[]`，也支持顶层 `spans` 数组。请求关联字段识别 `gen_ai.request.id`、`request_id` 和 `req_id`。Span 缺少 request id 或有效纳秒时间戳时会被跳过。

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

SGLang 可用 `--enable-trace --otlp-traces-endpoint ...` 导出请求 trace。Adapter 将根 span 映射为请求到达/完成，并识别已观测到的 `prefill_waiting`、`prefill_forward`、`chunked_prefill` 和 `decode_forward` 阶段。其它 stage 以 `FRAMEWORK_SPAN` 保留，不会丢弃，也不会被猜测性地归入 prefill/decode。

## 已知限制

- OTel spans 不一定携带 Prefix Cache token 级命中、KV block 分配/释放/淘汰、Scheduler batch 等数据；缺失字段必须由显式 trace 事件补充。
- Adapter 不反序列化 SGLang 的 pickle request dump。该数据用于 request replay，不是统一的低层 runtime trace。
- 导出的时间戳必须是 Unix 纳秒；Duration 属性必须是秒。没有这些字段就不合成阶段时间。

框架选项和观测字段会随版本变化。当前映射围绕 vLLM 0.29.0 与 SGLang 0.5.20 的已安装环境及其 OpenTelemetry/trace schema；引入其他版本时需更新示例和兼容性检查。
