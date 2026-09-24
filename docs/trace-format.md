# Trace 与 Workload JSONL 格式（v1）

文件采用 UTF-8，每行一个 JSON object；空行允许，字段错误报告行号。时间戳不得为负。未知扩展字段可保留，但核心必填字段不可缺失。

## Workload

```json
{"request_id":"req-001","timestamp":0.012,"input_token_ids":[1,2,3,4],"output_tokens":128}
```

`timestamp` 是以秒为单位的相对到达时间，可为整数或有限非负小数；回放时排序依据为 timestamp，再以原始行序稳定打破平局。Token ID 是非负整数，`output_tokens` 是非负整数。

## Trace

```json
{"timestamp_ns":12000000,"event_type":"REQUEST_ARRIVED","request_id":"req-001","input_tokens":4}
```

v1 事件类型包括：`REQUEST_ARRIVED`、`REQUEST_QUEUED`、`REQUEST_SCHEDULED`、`PREFIX_LOOKUP`、`KV_ALLOCATE`、`KV_REUSE`、`KV_FREE`、`KV_EVICT`、`PREFILL_STARTED`、`PREFILL_FINISHED`、`DECODE_STEP`、`REQUEST_FINISHED`。字段按事件扩展；不适用的字段省略。

`REQUEST_FINISHED` 可包含来源明确、以整数纳秒表示的实测时长：`queue_ns`、`prefill_ns`、`decode_ns`、`ttft_ns` 和 `e2e_ns`。请求分析优先使用这些观测值；字段缺失时才按事件边界推导相应阶段。输入 token 数可放在 `REQUEST_ARRIVED.input_tokens`，输出 token 数可放在 `REQUEST_FINISHED.output_tokens`。

KV block 状态语义：`KV_ALLOCATE` 要有 `block_id` 和正整数 `token_count`，并把一个引用关联到事件的 request；`KV_REUSE` 为该 request 增加引用；`KV_FREE` 只释放该 request 的引用，零引用 block 可留在缓存；`KV_EVICT` 仅允许淘汰零引用 block。非法迁移会报错。`FRAMEWORK_SPAN` 可保留 Adapter 尚未语义映射的 span 名称和时长。

## 分析报告的来源标记

请求报告新增 `latency_sources`，以 `queue_ns`、`prefill_ns`、`decode_ns`、`ttft_ns`、`e2e_ns` 为键：

- `OBSERVED`：输入 `REQUEST_FINISHED` 声明的有效实测时长。此标记说明数值来自输入字段，不代表 InferScope 独立验证了采集环境。
- `DERIVED`：由已有事件边界相减推导。TTFT 以 Prefill 结束为近似边界，Decode 为请求结束减 Prefill 结束，不能据此宣称精确的首 token 时间或模型执行时间。
- `UNKNOWN`：数据不足或边界顺序无效；数值仍为 JSON `null`。

`summary` 同时输出 `latency_source_counts`，分别统计每个指标的三种来源；`inspect` 使用相同的请求分析结果。旧数值字段保持兼容。

vLLM Adapter 用时长重建的排队、调度和 Prefill 事件携带 `timestamp_source: "DERIVED"`。`trace` 保留这一属性，文本模式也显示时间来源；没有时间来源标记的输入显示 `UNKNOWN`。

`replay`、`compare` 中每个策略报告及其 `workload_reuse` 新增 `analysis_mode: "SIMULATED"`，文本模式标为“离线模拟”。所谓 actual reuse 是当前模拟策略得到的复用量，不是线上测量值。

Python 直接构造数据对象与 JSONL 输入执行相同的核心数据约束：纳秒时间必须是非负整数，拒绝布尔值；workload 秒数必须有限且非负；token ID 和输出 token 数必须为非负整数。缓存 block 大小必须为正整数，容量可为零；两者拒绝布尔和浮点值。事件扩展属性不得覆盖核心字段。

## 版本约定

Trace 文件可在每条记录中用可选 `schema_version` 指定格式版本；省略时视为 v1。未知主版本拒绝处理。后续需要兼容旧版时提供显式迁移，不静默改变字段意义。
