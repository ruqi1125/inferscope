# InferScope 总体实施路线（唯一权威）

> 规划基线：v1.0（2026-09-24）
> 本文是 InferScope 产品范围、里程碑顺序和验收门槛的唯一权威来源。`README.md` 和 `architecture.md` 只作摘要或引用，不维护另一套路线。除非用户明确批准范围变更，或实测证据证明当前路线不可行，否则不得悄悄改变本文的产品目标、Agent 选择或阶段顺序。

## 1. 产品目标

InferScope 是轻量级的 LLM Serving Runtime Inspector，帮助开发者基于可追溯证据回答：**一次推理请求在 Serving Runtime 中经历了什么，为什么呈现出这样的延迟和缓存行为？**

长期分析对象固定为：

- Request 生命周期与请求关联；
- Queue、Prefill、Decode、TTFT、E2E 等延迟；
- Prefix Cache 命中与复用；
- KV Cache 分配、复用、释放、驱逐和容量变化；
- Scheduler 行为；
- workload 回放与缓存策略比较。

项目最终通过真实 Serving Runtime 的可观测数据分析线上行为，同时保留一个边界清楚的离线模拟模式，用于理解 workload 与缓存策略。二者不得混为一谈。

## 2. 固定决策与边界

1. **Runtime Inspector 是产品主线。** InferScope 不构建 Agent，不定义 Agent 工作流，也不把 Agent 评测平台作为产品目标。
2. **Aider 是真实验收对象。** 按此前决定使用现成 Aider，在项目达到验收门槛后执行固定编码任务；只做必要的模型端点配置，不自创工作流。
3. **接入顺序固定为 vLLM → SGLang。** 先把 vLLM 的采集、映射和分析链路做实，再按相同数据契约接 SGLang。首批兼容性基线为 vLLM 0.29.0 与 SGLang 0.5.20；扩展版本须有对应样例和测试。
4. **CLI 优先。** 输出人类可读摘要和机器可读 JSON；不建设复杂 Web 前端或监控大盘。
5. **事实按来源分层。** 每项结果必须能够区分真实观测、离线模拟、推导值和未知值。缺少直接证据时使用 `UNKNOWN`，不得合成逐请求事实或因果结论。
6. **Prometheus 不是产品主线或验收前置。** 如确有需要，可导入服务级聚合指标作背景信息；不得把聚合值归因到单个请求，也不替代 Prometheus/Grafana。
7. **不实现 Serving Engine、GPU Profiler、自动优化、训练分析或复杂平台。** 不为接入框架而无审批地维护引擎分支或大范围修改其内部实现。

## 3. 当前基线

截至本规划基线，当前实现已有：

- 统一事件与 JSONL 输入校验；
- 请求/延迟摘要、单请求查看、事件 trace 和 CLI；
- KV block 事件分析器；
- HashBlock 与 Radix Prefix Cache 模拟、workload replay 和 compare；
- vLLM 与 SGLang 的离线 OpenTelemetry JSON Adapter；
- 当前可见工作副本中的 16 个自动化测试通过。

这些完成项建立了可运行的离线基础，但不代表已采集真实 Runtime 的完整缓存或调度行为。当前仍未完成：

- 从运行中的 Serving 服务稳定采集并端到端校验 trace；
- 接入可验证的逐请求 Scheduler、Prefix Cache 和 KV block 事件；
- 基于真实事件完成 lost-reuse 的因果归因；
- 运行 Aider 真实任务并核对 Agent 请求与原始 Runtime trace；
- SGLang 的真实运行端到端验收。

开始后续代码工作前，先确定唯一工作副本并核对本地、远端与 GitHub 的提交关系。仓库同步问题属于交付前置，不得通过强制覆盖或重写未知提交历史来处理；项目提交信息继续使用中文。

| 里程碑 | 当前状态 | 依据/说明 |
|---|---|---|
| M0 代码源与交付基线 | 已完成 | 本地、GitHub `main` 与服务器 `/home/nas511/zhangruqi/deeper` 的 `main` 均已同步且工作区干净。合并提交保留原本地与 GitHub 两侧历史，未强推；服务器通过 Git bundle 快进同步。同步前后离线测试均为 16 项通过。 |
| M1 离线分析核心 | 已完成 | 直接构造和 JSONL 输入共用校验；Workload 与 vLLM OTel 十进制时间按纳秒精度稳定换算，并将纳秒结果限制为最多 4096 位以防极端指数造成无界分配；重复生命周期边界显式报告，歧义推导值保持 UNKNOWN；请求、Adapter、KV、Replay/Compare 报告标注实测/推导/模拟来源；Radix 淘汰会回收无效 trie 路径。Hash/Radix 容量与驱逐按前缀 block entry 计。全套测试 74 项及 3 个子用例在本地和远端 `agent-235` 环境通过。 |
| M2 vLLM 真实 Trace 链路 | 已完成 | 在现有 vLLM 0.29.0 环境对既有 Llama-3-8B-Instruct 仅发送一条合成请求；采集到唯一 `llm_request`（聚合 OTLP 共 110 spans），将 request id、起止时间、14/8 token 和实际提供的五项 latency 与 Adapter/CLI 逐项核对。脱敏白名单 fixture：`tests/fixtures/vllm-0.29.0-otel.json`；`adapt`、`summary`、`inspect` 均成功；新增 fixture/CLI/UNKNOWN 回归及全套测试 78 项和 3 个子用例通过。未启用 detailed traces；KV、Prefix Cache、Scheduler 仍未由本次 trace 证明，按路线留待 M3。 |
| M3 真实 Cache/Scheduler 观测 | 已完成（vLLM 0.29.0 可安全接入范围） | 原生 StatLogger 的逐请求缓存 token（含 request ID/回调采集时间）、engine 级 Scheduler/Prefix Cache 统计、可选 KV 淘汰样本均保留来源与 scope；`summary --runtime-stats` 支持多 engine 文件，按唯一 request ID 关联，缺失/重复/跨 engine 歧义保持 UNKNOWN。真实重复前缀实测首次 0/360、再次 352/360 cached tokens，PrefixCacheStats 720 queries/352 hits，20 条快照和 1 条淘汰样本在脱敏 fixture；全量 105 项及 3 个子用例通过。逐请求 Scheduler 与 block ID/完整生命周期不可得，明确不宣称覆盖；跨来源真实 ID 联表留给 M4 核验。 |
| M4 Aider + vLLM 验收 | 未通过（Llama 任务未修改代码；Qwen 服务与 trace 成功，但 Aider 输出不符合编辑协议且超上下文预算，接受测试未运行） | 固定任务及隔离办法见 `docs/m4-aider-acceptance.md`。Llama 3 8B 的 8,192 context 不足；Qwen3.6-27B-GPTQ-Int4 在 vLLM 0.29.0、双卡、32,768 context 下成功 ready。Aider 0.86.2 产生 4 条 `llm_request` trace（另有 5 次 HTTP 200，二者数量差异待查），但持续输出解释/整文件草稿，Aider 将解释当文件名并由 flake8 捕获 SyntaxError；上下文估算升至 45,160，超过 28,672 元数据上限，遂由 Ctrl+C 停止。预置 `pytest -q` 未运行，不能判定任务效果；临时 Aider 输出未并入产品分支。当前规划原文实测哈希与历史记录哈希不一致，下一轮前须先厘清，并明确模型编辑格式/上下文预算的可复现配置；不得原样重跑或声称 M4 通过。完整测试基线 105 项及 3 个子用例通过，不能替代 M4。 |
| M5 SGLang 实测 | 未开始 | 目前只有离线 Adapter。 |
| M6 发布质量 | 未开始 | 按前序里程碑产出更新。 |

## 4. 唯一实施顺序

### M0：固定代码源与交付基线

**工作：** 确认唯一权威 Git 工作副本；核对远端服务器、GitHub 与本地的分支/提交关系；记录当前测试结果和已实现能力。

**通过条件：** 后续修改位置明确；提交历史已安全对齐或明确记录尚未解决的同步限制；没有用强制推送覆盖未知提交。

### M1：稳固离线分析核心

**工作：** 在现有实现上做有证据支持的正确性修补，不重写已可工作的模块。核对事件状态迁移、时间单位、缺失阶段处理、缓存容量/对齐口径、Replay 确定性及报告中的来源标记。

**通过条件：** 示例和自动化测试可重复运行；`summary`、`inspect`、`trace`、`replay`、`compare` 的输出口径一致；模拟结果明确标记为模拟，不冒充 Runtime 观测。

### M2：打通 vLLM 真实 Trace 链路

**工作：** 先以 vLLM 0.29.0 为基线，从真实运行服务导出 OpenTelemetry trace，保存可复现的脱敏 fixture，经 Adapter 转为统一事件，再由 CLI 分析。允许用少量受控请求排查采集链路；这只是技术 smoke test，不是 Agent 效果评测。首个应用层端到端验收仍是 M4 的现成 Aider。

**通过条件：** 原始 span 与转换事件中的 request id、时间戳、token 数和已提供延迟字段可逐项核对；请求数及统计可回溯到输入 trace；未提供的阶段保持未知。Prometheus 采集不是此里程碑的必要条件。

### M3：补齐真实 Cache 与 Scheduler 观测

**工作：** 基于目标 vLLM 版本实际提供的事件/接口，接入有证据支持的 Prefix Cache、KV 生命周期和 Scheduler 数据，并将其映射到统一事件模型。优先使用 Runtime 已提供的可观测数据；如原生数据不足，先报告具体缺口及影响，再提出最小、可隔离的可选采集方案。

**通过条件：** 报告能区分逐请求观测、服务级聚合值、模拟值和未知值；Cache/Scheduler 分析保留原生来源、scope、request/engine ID 及采集时间，可回查原始 JSONL。若必须大范围改 Runtime 内部实现才能取得逐请求 Scheduler 或完整 block 生命周期，记录为不支持并保持 UNKNOWN；未经用户明确批准不改 Runtime 内部，不把不可观测字段写成已完成。

### M4：Aider + vLLM 真实验收

**启动门槛：** M2 通过；数据与 request id 关联稳定；原始 trace 和分析报告已核对；M3 中未支持的字段会如实显示 `UNKNOWN`；Agent 运行环境可隔离并清理。

**工作：** 使用现成 Aider，在临时仓库执行固定编码任务，经 vLLM 服务调用模型。记录任务是否通过测试，以及实际产生的请求、token 和延迟；分析结论回查原始 trace。Aider 的任务流程保持框架原样，不为 InferScope 改造成自定义 Agent。

**通过条件：** 实验可重复；任务结果、Agent 请求与 Runtime trace 能对应；报告只对有数据支持的延迟和缓存行为作结论。

### M5：SGLang 适配与同类验收

**工作：** 按稳定的统一事件模型实现/完善 SGLang Adapter；以真实 SGLang trace 做格式和指标核对；用与 M4 同类的 Aider 编码任务验证端到端流程，并记录与 vLLM 的可观测性差异。

**通过条件：** 有明确的框架版本支持说明、可复现输入和自动化 Adapter 测试；共同字段语义一致，框架特有或缺失字段被显式标注。

### M6：文档与发布质量

**工作：** 整理安装与 CLI 使用说明、支持版本矩阵、脱敏 trace fixture、Aider 验收步骤、已知限制和机器可读报告示例。完善必要的基准脚本，但不扩建监控平台。

**通过条件：** 新用户可按文档从样例运行离线分析，并可在支持的 Runtime 上复现实测流程；发布说明不夸大数据覆盖能力。

## 5. Aider 验收前检查表

- vLLM 真实 trace 已导出并能被当前 Adapter 解析；
- request id、时间戳、token 统计及可用延迟与原始 trace 对得上；
- 缺少 Scheduler/Prefix/KV 事件时显示为未知，不通过 Prometheus 聚合值推断单请求原因；
- 任务仓库与运行目录隔离，实验结束可清理；
- 固定任务、Serving 配置、模型配置和输入记录齐全，重复运行可以比较；
- 报告同时保留任务验收结果与 Runtime 观测结果，不把两者混成单一分数。

## 6. 变更控制：防止路线再次偏移

- `docs/implementation-plan.md` 是唯一产品路线来源；进度更新只修改本文对应的状态和证据。
- `docs/architecture.md` 只描述架构、数据契约和模块边界；`README.md` 只摘要目标、当前能力并链接到本文，不复制一套独立里程碑。
- 新想法先归入既定目标或非目标。若会改变产品目标、Agent 框架、Runtime 接入顺序、验收门槛或阶段依赖，先写明证据、收益、代价和受影响里程碑，得到用户明确批准后再修改本路线。
- 遇到技术阻碍时，报告已验证事实、影响范围和可选方案；暂停受影响的里程碑，不以静默换目标的方式继续。
- 每个里程碑结束时更新“当前基线”或阶段状态，并附可复核的测试/实验依据。不得仅因实现了 Adapter 或 demo 就宣称真实 Runtime 观测已完成。

## 7. 技术与语义约束

- Python 3.10+；运行时依赖优先标准库；pytest 用于测试；Serving Framework 依赖保持可选。
- JSONL 是首期数据格式；内部事件时间使用整数纳秒；Workload 十进制秒值在回放排序时精确换算到最近纳秒，同值保持输入顺序。
- Hash block 只复用完整对齐 block；Radix 与 Hash 的容量、entries 和 blocks 口径必须在报告中说明。
- 非法输入不得静默丢弃；错误应定位到源文件和行号。
- Cache miss、lost reuse 和利用率只有在数据与定义充分时才分类/计算；否则保留 `UNKNOWN`。
