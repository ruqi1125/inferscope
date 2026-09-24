# InferScope 架构

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

Workload 每行描述一个请求：`request_id`、以秒为单位的相对 `timestamp`、整数 `input_token_ids` 和非负 `output_tokens`。Trace 每行是一条有 `timestamp_ns`、`event_type`、`request_id` 和可扩展字段的事件。输入校验采用 fail-fast，并报告文件名和行号。事件时间统一使用整数纳秒；Workload 在回放时按十进制值换算为最近整数纳秒，纳秒精度相同时保持输入顺序。vLLM OTel duration 同样按十进制原值精确换算为最近整数纳秒。

分析区分输入实测值、边界推导值、离线模拟值和未知值。重复生命周期边界会显式列出，依赖歧义边界的推导延迟记为 `UNKNOWN`。Prefix miss 原因只能在证据充分时分类；容量未知时不报告利用率。Cache 容量和 eviction 以完整前缀 block entry 计；Radix 内部 token trie 节点数另行计数，淘汰 entry 时回收无用路径。指标输出带上配置和来源，以便复现。

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

## 路线文档

产品目标、当前完成度、固定范围、里程碑顺序、验收门槛和路线变更规则统一维护在 [`docs/implementation-plan.md`](implementation-plan.md)。本文只维护架构、数据契约与模块边界，不复制另一套阶段列表或路线状态。

## 非目标

不实现完整 Serving Engine、CUDA/Nsight profiler、Prometheus/Grafana 替代、自动优化、训练分析或复杂 Web 前端。
