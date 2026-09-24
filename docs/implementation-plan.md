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

建立能运行的项目骨架和核心离线闭环：严格读取 workload/trace JSONL，模拟 hash/radix prefix cache，生成 replay 汇总，提供 summary/inspect/replay/compare CLI，并提供 demo 与架构/格式文档。真实框架 adapter、完整 KV 时间线和 lost-reuse 因果归因按阶段路线继续展开。

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
