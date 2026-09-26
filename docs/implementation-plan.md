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

### 1.1 原始需求附件覆盖与最终验收

本节将最初的 InferScope 需求附件固定为最终产品范围的追溯基准。下表中的“当前证据/缺口”描述已实现和已验证到什么程度，不代表最终范围；后续进度只能据证据更新，不能把当前不可观测或 `UNKNOWN` 静默改成永久不做。

| 附件能力 | 最终验收标准 | 当前证据与缺口（按本规划基线） |
|---|---|---|
| Request Trace 与延迟 | 对支持的 Runtime/版本，将可获得的请求事件按 request ID 重建从进入、排队、调度、Prefix 查询、KV 分配、Prefill、Decode 到完成的时间线；报告 Queue、Prefill、Decode、TTFT、E2E 等有证据的延迟，并保留来源。 | vLLM trace 已核对 request ID、token 数及五项已提供 latency；当前真实数据尚不能重建完整逐请求调度、Prefix 与 KV 阶段。缺少事件的阶段必须标 `UNKNOWN`。 |
| Prefix Cache Inspector | 有来源地报告 queried、matched/reused、recomputed tokens 和 hit ratio；miss 原因可分类为 `COLD_MISS`、`PREFIX_DIVERGENCE`、`BLOCK_ALIGNMENT`、`EVICTED`、`CAPACITY_PRESSURE` 或 `UNKNOWN`。原因归类须可追溯且不得由聚合指标臆断。 | vLLM 重复前缀实测获得逐请求 cached tokens 与服务级 Prefix 统计；尚未证明完整逐请求查询/匹配数据及可靠 miss 原因归因。 |
| KV Cache Inspector | 在支持的数据源上重建带 block/request 关联的 `ALLOCATE`、`REUSE`、`FREE`、`EVICT` 变化，计算 allocated/reused/evicted blocks、peak usage、utilization，并提供可检查的 Cache Timeline；说明计数和容量口径。 | 当前 vLLM 只取得有限淘汰样本，缺少 block ID 与完整逐请求生命周期；故完整 Timeline、利用率及请求归因仍是缺口。 |
| Scheduler 分析 | 消费统一事件中的逐请求调度行为（至少覆盖可观测的 queued/scheduled 及相关批次/调度事件），分析等待和调度行为；数据源不提供的事件显式标未知。 | 当前只有 engine/service 级 Scheduler 统计，没有逐请求 queued/scheduled 事件，不能据此推断单请求行为。 |
| Workload 与 lost-reuse 分析 | 分析请求/输入 token 总量、potential 与 actual reuse/hit ratio、computed/cached tokens；在证据充分时按 eviction、prefix divergence、block alignment 等解释 lost reuse，否则将相应部分列为 `UNKNOWN`。 | 离线 workload 与缓存模拟可分析策略下的复用；基于真实 Runtime 事件的完整 potential/actual 对照及 lost-reuse 因果归因尚未完成。 |
| Replay、模拟器与策略比较 | 从 workload JSONL（含 request ID、时间戳、input token IDs、output token 数）确定性回放；提供 HashBlock 与 Radix 两类模拟，覆盖 block/hash/alignment/eviction 以及 longest-prefix、sharing、reference count、LRU/eviction；同一 workload 可比较策略并报告口径。模拟结果必须明确标为模拟，不宣称等同真实 Runtime。 | HashBlock、Radix、Replay 和 Compare 已实现并有自动化测试；后续持续以原始需求中的行为和报告口径作为验收基准。 |
| 统一事件模型与 Adapter | Core、cache、analyzer 不依赖 Serving Framework；Runtime 差异由 Adapter 隔离，统一事件保留时间、request/engine/batch/block 关联（源数据提供时）、scope、来源和证据；按既定 vLLM → SGLang 顺序验证。 | 统一事件模型及 vLLM/SGLang 离线 OTel Adapter 已有；vLLM 真实采集仍有上述事件缺口，SGLang 真实端到端尚未开始。 |
| CLI、报告与最终流程 | CLI 至少支持 `summary`、`inspect`、`trace`、`replay`、`compare`；对真实采集和离线模拟给出可读且可追溯的报告，适合的命令提供机器可读 JSON。可选静态报告/TUI，不以复杂 Web 为目标。最终流程既可从真实 Runtime Adapter 分析，也可从 workload 回放、分析并比较策略。 | 主要 CLI 与离线流程已存在；vLLM+Aider 两轮真实请求/延迟核验通过；Cache/Scheduler 完整实测及 SGLang 流程仍待完成。 |

**范围解释：** `UNKNOWN` 是某次数据或当前 Adapter/Runtime 版本尚无充分证据时的正确结果，不等于删除附件要求。对每个缺口，后续应先检查 Runtime 原生接口，再评估最小、可隔离、可选且按版本维护的采集方案。若确需侵入式 Runtime 修改，先列出具体字段、方案、风险和替代路径，取得用户明确批准后再实施；在批准前保持现状并如实标未知，不得宣称该能力已验收，也不得悄悄缩减最终目标。附件给出的仓库树是实现参考，不要求逐路径照搬；第 2 节的产品边界仍然有效。

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
| M4 Aider + vLLM 验收 | 已通过（Qwen3.6-27B-GPTQ-Int4；同一配置两次独立运行均完成任务并通过测试） | 固定任务、配置与白名单证据见 `docs/m4-aider-acceptance.md`。Aider 0.86.2 显式使用原生 `diff` edit format、关闭 repo map，并将模型输入上限设为 28,672；同一预置基线两轮均产出代码修改，内置及独立全套测试均为 105 passed、3 个子测试通过。16 个 Agent HTTP 200 completion 全部与 `llm_request` span 按 ID 一一匹配；InferScope 两轮 `adapt → summary` 均完整保留 request ID、prompt/completion tokens 及五项已观测 latency，逐项差异为 0。此前 Llama/Qwen 失败记录保留为排障历史；Aider 结束时有非阻断 summarizer shutdown 告警。未据此宣称本轮观测到 Scheduler/Cache/KV 请求级数据。 |
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

**通过条件：** 报告能区分逐请求观测、服务级聚合值、模拟值和未知值；Cache/Scheduler 分析保留原生来源、scope、request/engine ID 及采集时间，可回查原始 JSONL。原生接口不足时，须按 1.1 节记录缺口并评估最小、可隔离的可选采集路径；在任何获批方案实现并验证前，相关字段保持 UNKNOWN，不能据此把附件中的最终能力从范围中移除。未经用户明确批准不改 Runtime 内部实现，不把不可观测字段写成已完成。

### M4：Aider + vLLM 真实验收

**启动门槛：** M2 通过；数据与 request id 关联稳定；原始 trace 和分析报告已核对；M3 中未支持的字段会如实显示 `UNKNOWN`；Agent 运行环境可隔离并清理。

**工作：** 使用现成 Aider，在临时仓库执行固定编码任务，经 vLLM 服务调用模型。记录任务是否通过测试，以及实际产生的请求、token 和延迟；分析结论回查原始 trace。Aider 的任务流程保持框架原样，不为 InferScope 改造成自定义 Agent。当前固定任务的两次复现均已按 `docs/m4-aider-acceptance.md` 验收通过；后续若改模型、基线或编辑配置，须作为新实验记录，不能覆盖本次证据。

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
