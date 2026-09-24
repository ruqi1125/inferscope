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

vLLM 可通过 `--otlp-traces-endpoint` 导出 OpenTelemetry spans；部分细粒度模型阶段需要 `--collect-detailed-traces`，这会引入额外开销。vLLM 0.29 的请求 span 名为 `llm_request`，即使它带有父 span，也按请求 span 处理。Adapter 使用其起止时间，并将 `gen_ai.latency.time_in_queue`、`gen_ai.latency.time_to_first_token`、`gen_ai.latency.time_in_model_prefill`、`gen_ai.latency.time_in_model_decode` 和 `gen_ai.latency.e2e` 归一化为纳秒后写入 `REQUEST_FINISHED`。请求摘要优先采用这些实测时长；没有实测字段时才从阶段事件边界计算。Prompt/completion token 数分别映射到请求到达/完成事件。其它非请求 span 以 `FRAMEWORK_SPAN` 保留名称和时长，不推断其业务含义。

## SGLang

SGLang 可用 `--enable-trace --otlp-traces-endpoint ...` 导出请求 trace。Adapter 将根 span 映射为请求到达/完成，并识别已观测到的 `prefill_waiting`、`prefill_forward`、`chunked_prefill` 和 `decode_forward` 阶段。其它 stage 以 `FRAMEWORK_SPAN` 保留，不会丢弃，也不会被猜测性地归入 prefill/decode。

## 已知限制

- OTel spans 不一定携带 Prefix Cache token 级命中、KV block 分配/释放/淘汰、Scheduler batch 等数据；缺失字段必须由显式 trace 事件补充。
- Adapter 不反序列化 SGLang 的 pickle request dump。该数据用于 request replay，不是统一的低层 runtime trace。
- 导出的时间戳必须是 Unix 纳秒；Duration 属性必须是秒。没有这些字段就不合成阶段时间。

框架选项和观测字段会随版本变化。当前映射围绕 vLLM 0.29.0 与 SGLang 0.5.20 的已安装环境及其 OpenTelemetry/trace schema；引入其他版本时需更新示例和兼容性检查。
