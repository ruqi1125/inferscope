# M3 trace 与 vLLM 原生统计关联、M4 启动核验

## 目标与边界

沿用 `docs/implementation-plan.md` 唯一路线。先补齐 M3 产物之间的 request ID 关联能力，使 CLI 能把 vLLM 原生逐请求缓存观测与 OTel 请求摘要并列呈现；服务级 Scheduler/Prefix Cache/KV 样本仍按 engine scope 保留，不伪装为单请求数据。然后核验 M4 Aider + vLLM 既定启动门槛和远端资源，能安全运行时就执行固定、可复现的真实 Agent 任务；若共享 GPU 正被占用，不中断或争抢其进程，完成所有可做的本地准备并记录具体阻塞证据。

不改造 Aider 工作流、不修改 Runtime 内部、不产生伪造观测、不更改远端共享服务。所有新增请求关联只按精确 request ID；重复、缺失或跨 engine 冲突均保持 UNKNOWN 并显式计数。

## 任务 1：新增关联行为测试（已完成）

- [x] 覆盖精确 ID 匹配及逐请求缓存 tokens/fraction 的白名单暴露。
- [x] 覆盖 trace 无对应 native observation、native observation 无 trace、重复 observation、跨 engine 同 ID 四种边界；不能误关联或把未知写成零。
- [x] 覆盖 CLI `summary --runtime-stats FILE --json`，并确认不带新参数时原输出契约不变。
- [x] 覆盖文本报告对 request scope 与 engine scope 的区分，并验证两份既有真实 fixture 不会被错误合并。

## 任务 2：实现纯函数关联及 CLI（已完成）

- [x] 新增可单测的关联函数，返回新 summary 对象。
- [x] 在 trace request 中加入明确 native-observation 状态；顶层保留原生 request/engine 报告及 matched/unmatched/ambiguous 计数。
- [x] `summary --runtime-stats` 校验并接收多个 JSONL；原有不带参数路径不变。
- [x] JSON 与文本输出都显示来源和 scope，不添加推导时间戳或 per-request Scheduler 结论。

## 任务 3：文档与本地验收（本地验收完成）

- [x] 更新 M3/M4 文档、总体计划、架构边界和 CLI 用法，写清关联规则与 UNKNOWN 语义。
- [x] 聚焦回归与全量 pytest 通过（105 项及 3 个子用例）；仓库未配置额外 lint/type 工具。
- [x] `git diff --check` 无空白错误；M2/M3 真实 fixture 未发生跨来源误关联。

## 任务 4：按 M4 门槛核验并执行真实 Aider 验收（资源阻塞）

- [x] 只读检查 0.29.0 vLLM 环境、端口和 GPU；两张卡分别使用约 16.3/24 GB 和 15.0/24 GB，默认环境及已检查 venv 未发现 Aider。
- [x] 按 M4 固定任务写好隔离方案；没有停止/重配共享服务，也没有启动竞争任务。
- [ ] GPU 可安全使用且独立 Aider venv 准备好后，运行 Aider 真实任务，临时保留 raw trace，脱敏提取 fixture、分析 JSON、diff 与测试结果，随后清理临时目录。
- [ ] 不对 per-request Scheduler 或完整 KV lifecycle 作无证据结论。

## 任务 5：集成（已完成）

- [x] 按可安全采集范围完成 M3，并明确保留的观测缺口。
- [x] 中文提交已快进合并并推送 GitHub `main`；NAS `/home/nas511/zhangruqi/deeper` 也快进同步至 `7ade70e`。
- [x] 本地与 NAS 全量测试均为 105 项及 3 个子用例通过；NAS 工作区干净。
