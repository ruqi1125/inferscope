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

## 版本

Trace 文件可在每条记录中用可选 `schema_version` 指定格式版本；省略时视为 v1。未知主版本拒绝处理。后续需要兼容旧版时提供显式迁移，不静默改变字段意义。
