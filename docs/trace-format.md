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

## 版本

Trace 文件可在每条记录中用可选 `schema_version` 指定格式版本；省略时视为 v1。未知主版本拒绝处理。后续需要兼容旧版时提供显式迁移，不静默改变字段意义。
