# InferScope 架构与整体路线

## 产品目标

InferScope 是轻量级的 LLM Serving Runtime 分析工具，帮助开发者回答一次推理请求为什么表现成这样。它覆盖请求生命周期、Prefix Cache、KV Cache、Scheduler、Prefill/Decode 延迟、workload replay 和缓存策略比较；它不替代 GPU profiler、监控平台或 Serving Framework。

## 架构原则

1. `core` 定义与框架无关的事件、请求和 trace 契约。
2. `storage` 负责 JSONL 读取、写入、校验和版本化。
3. `cache` 提供可独立模拟的 Prefix Cache 与 KV block 状态模型。
4. `replay` 将 workload 输入缓存模拟器，并产出统一事件流。
5. `analyzers` 只消费 InferScope Event Stream，不导入 vLLM/SGLang。
6. `adapters` 将框架可观测数据映射为统一事件；无法映射的字段保留未知状态。
7. `cli` 是主要入口，报告同时支持人类可读和 JSON 输出。

```text
Workload ──► Replay ──► Cache Simulator ──► Event Stream
                                              │
Trace JSONL ──────────────────────────────────┤
                                              ▼
                ┌──────────┬──────────┬──────────┬──────────┐
                Request   Cache     Latency   Workload   KV Cache
                Analyzer  Analyzer  Analyzer  Analyzer   Analyzer
                └──────────┴──────────┴──────────┴──────────┘
                                              │
                                              ▼
                                      CLI / JSON Report

vLLM / SGLang ──► Framework Adapter ──► Event Stream
```

## 核心数据契约

Workload 每行描述一个请求：`request_id`、以秒为单位的相对 `timestamp`、整数 `input_token_ids` 和非负 `output_tokens`。Trace 每行是一条有 `timestamp_ns`、`event_type`、`request_id` 和可扩展字段的事件。输入校验采用 fail-fast，并报告文件名和行号。事件时间统一使用整数纳秒；工作负载时间在入口处转换。

分析区分观测值与模拟/推断值。Prefix miss 原因只能在证据充分时分类；无法从事件流确认的情况返回 `UNKNOWN`。容量未知时不报告虚构的利用率。指标输出需带上配置和来源，以便复现。

## 模块边界

```text
core ◄── storage
  ▲       ▲
  │       ├── adapters
  ├── cache ── replay
  ├── analyzers
  └── cli ──► 各分析模块与报告
```

`core`、`cache`、`analyzers` 不依赖任何 Serving Framework；vLLM 和 SGLang 依赖只允许出现在各自 adapter 子包中，并作为可选依赖安装。

## 整体实施路线

### A. 契约与可执行骨架

完成 Python 包、版本化 JSONL 格式、统一 Event、输入错误诊断、CLI 入口和示例数据。验收：示例可读取，非法记录能准确定位。

### B. Trace 与请求分析

根据事件重建请求生命周期，支持 `summary`、`inspect`、`trace`。统计 Queue、Prefill、Decode 和 TTFT；缺失阶段以 unknown 表示，不跨越未知区间猜测耗时。

### C. KV Cache 生命周期分析

建模 ALLOCATE、REUSE、FREE、EVICT，校验状态迁移，重建 block 时间线，统计请求分配/复用、驱逐、峰值占用和有容量依据的利用率。

### D. Prefix Cache 模拟器

建立统一接口，实现按 block 对齐和容量/淘汰规则明确的 HashBlockCache，以及最长前缀匹配、共享节点、引用管理和 LRU 的 RadixPrefixCache。记录 matched/recomputed tokens 和 miss 原因；证据不足时为 UNKNOWN。

### E. Replay 与策略比较

支持 workload JSONL 的确定性回放；比较相同输入、相同容量约束下的 hash/radix 策略，报告命中率、cached/computed tokens、evictions、peak entries，并记录策略配置。

### F. Workload 复用归因

估算 workload 潜在复用与模拟得到的实际复用，拆分 eviction、divergence、alignment、unknown。定义严格的分母和口径；无法因果归因的 lost reuse 留在 unknown。

### G. Framework Adapter

离线 OpenTelemetry JSON adapter 已实现 vLLM 和 SGLang 的请求/已知阶段映射。后续补齐版本兼容、Prometheus 聚合指标导入和更多低层事件来源。Adapter 负责版本适配与映射，不向分析器泄漏框架对象；无数据字段不得合成。

### H. 报告与发布质量

完善 CLI 表格、JSON 报告、文档、演示 workload、基准脚本和兼容性说明。保持 CLI 为主，不建设复杂 Web 平台。

## 非目标

不实现完整 Serving Engine、CUDA/Nsight profiler、Prometheus/Grafana 替代、自动优化、训练分析或复杂 Web 前端。
