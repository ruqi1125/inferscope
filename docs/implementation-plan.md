# InferScope 实施计划

## 目标

逐步交付一个完整的 LLM Serving Runtime Inspector，先形成可以单机复现的 trace 分析和 workload replay 闭环，再适配真实 Serving Framework。

## 技术选择

- Python 3.10+，运行时依赖优先标准库。
- `pyproject.toml` 使用 setuptools 构建；CLI 入口 `inferscope`。
- JSONL 为首期输入/输出格式，时间内部统一整数纳秒。
- 单测使用 pytest；框架依赖作为可选 extra。
- 所有 Git commit message 使用中文。

## 阶段任务

1. **包骨架与数据契约**：建立包元数据、CLI 入口、Event/Workload 类型、JSONL 解析与示例。
2. **Trace 请求分析**：生命周期状态重建、summary/inspect/trace 命令、延迟阶段和缺失事件诊断。
3. **KV Cache 分析**：状态机、容量约束、时间线和 block 指标。
4. **Prefix Cache**：接口、HashBlockCache、RadixPrefixCache、LRU/容量/对齐语义及 miss 原因。
5. **Replay/Compare**：确定性 workload replay、cache 统计汇总与策略对比报告。
6. **Workload 复用分析**：潜在/实际复用口径和可证据支持的 lost reuse 分类。
7. **vLLM Adapter**：按支持版本适配 trace/API，映射至统一事件流。
8. **SGLang Adapter 与交付**：按可观测性实现适配器，补齐示例、基准和兼容性文档。

## 依赖关系

阶段 1 是全部模块的前置。阶段 2、3、4 在事件契约稳定后可并行开发；阶段 5 依赖 workload 和 cache；阶段 6 依赖 replay 输出与可说明的口径；阶段 7、8 依赖稳定 Event Model。CLI 与文档在各阶段同步扩展。

## 本轮实现范围

建立可运行的离线分析闭环：严格读取 workload/trace JSONL，模拟 hash/radix prefix cache，生成 replay/workload 复用汇总，分析 KV block 生命周期，提供 summary/inspect/replay/compare/adapt CLI，并提供 vLLM/SGLang OpenTelemetry JSON adapter、demo 与架构/格式文档。更完整的 KV 线上观测、Prometheus 导入和 lost-reuse 因果拆分按阶段路线继续展开。

## 当前进度与后续里程碑

- 阶段 1–5 已有可运行的离线基础：统一事件和 JSONL、请求/延迟与 KV 分析、Hash/Radix 缓存模拟、workload replay 和策略比较。它们目前不代表 Serving 运行时的真实缓存行为。
- 阶段 6 只有潜在/模拟复用汇总；Lost reuse 暂归为 `UNKNOWN`，因果分类尚未完成。
- 阶段 7–8 部分完成：vLLM/SGLang 的 OpenTelemetry JSON 可离线转换。vLLM 0.29 的 `llm_request` span 已映射到请求生命周期，并保留其实际队列、Prefill、Decode、TTFT、E2E 和 token 数。当前仍不负责启动/抓取线上服务，也没有导入 Prometheus 指标或逐请求 Scheduler/KV block 事件。

后续按以下顺序交付：

1. **真实运行时数据入口**：接入 vLLM trace 导出和 Prometheus `/metrics` 采集/导入；明确区分逐请求观测与服务级聚合指标，并标记采集配置和来源。
2. **真实事件与解释能力**：根据 vLLM/SGLang 可用事件逐步补充 Prefix Cache、Scheduler 和 KV 生命周期数据；只有有直接证据时才给出 miss/lost-reuse 原因。
3. **真实 Agent 验收**：前两步的输入、关联和报告稳定后，在隔离的临时仓库中运行现成 Aider 编程任务，经 vLLM 请求并采集 trace，检查 InferScope 的时延、请求关联和复用分析是否与原始数据一致；再扩展到 SGLang。
4. **交付质量**：补齐版本兼容矩阵、可复现样例、基准结果和机器可读报告。

## 关键语义与风险

- Hash block 仅复用完整对齐 block；尾部 token 按未命中处理。
- 容量单位固定为缓存 block 数；Radix 容量按唯一缓存 token 计数并向上换算为 block，报告 entries 与 blocks 时标清口径。
- LRU 逐个 block/前缀单元淘汰；radix 子树只在无活动引用时可裁剪。
- CLI 输入错误不静默丢行；错误包含源路径与行号。
- 潜在复用及 miss 原因必须标注模拟/估算来源，不能伪装成线上观测。

## 首轮验收

- `python -m inferscope --help` 列出命令。
- `replay examples/demo.jsonl` 输出请求数、token 数、命中率、淘汰和峰值容量。
- `compare` 对同一 workload 运行 hash 与 radix 并排输出。
- `summary`/`inspect` 能解析示例 trace，并对缺失阶段显示 unknown。
- 分析与缓存代码不导入 vLLM/SGLang。
