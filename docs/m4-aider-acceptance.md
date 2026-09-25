# M4：Aider + vLLM 固定验收

本文件细化 `docs/implementation-plan.md` 的 M4，不改变框架选择或阶段顺序。Aider 按原生交互方式使用；InferScope 只分析其请求经过 vLLM 时的真实 trace，不自建 Agent 工作流。

## 当前状态

M4 尚未执行。2026-09-26 再次只读检查 `100.120.119.84`：GPU 0 空闲约 7.0 GiB、GPU 1 空闲约 8.9 GiB，GPU 1 利用率 97%；固定候选 Llama-3-8B-Instruct 无法按既定配置安全启动。OTLP/vLLM 实验端口未占用；系统 PATH 和已检查的用户目录深度内未找到 Aider，`agent-235/.venv` 中有 vLLM 但没有 Aider。远端仓库仍为 `4857b5a` 且工作区干净。没有停止或复用其他进程，也没有安装 Aider/启动服务。

同日对当前代码执行 `python -m pytest -q`：105 passed，3 subtests passed。另用随仓库保存的 vLLM 0.29.0 脱敏 OTel trace 与 native-stats JSONL 实际运行 `adapt → summary`：6 个事件形成 1 个请求，五项延迟均标为 `OBSERVED`；trace 请求与 native 统计属于不同采集轮次，关联报告为 0 matched、各自 unmatched，逐请求缓存值保持 `UNKNOWN`，engine 级 Scheduler/Prefix Cache/KV 淘汰仍单独报告。该检查验证的是已保存样本的 CLI 行为，不是新的 live inference 或 Aider 验收，不能代替 M4。

## 固定编码任务

以 `985c1e2`（本次 M3 开发开始前的 InferScope 基线）为临时仓库基线。在临时 checkout 中预置本项目 `tests/test_runtime_trace_correlation.py` 作为接受测试，然后把以下任务原文交给 Aider：

> 为 InferScope 的 `summary` 命令添加可选的 vLLM 原生统计关联能力，支持重复指定 `--runtime-stats FILE` 以读取多个 engine 的 JSONL。只按 trace 与 native 统计中的精确 request ID 关联；仅当两侧均唯一且缓存 token 来源为 OBSERVED 时，才将逐请求 cached tokens 与 fraction 加入 summary。缺失、重复回调、跨 engine 同 ID 冲突或统计未知时必须保留 UNKNOWN，并提供关联计数。服务级 Scheduler、Prefix Cache 与 KV 淘汰数据必须继续以 ENGINE scope 单独报告，绝不能归因到单请求。保留现有不带新参数时的 CLI JSON 契约，不增加运行时依赖。完成实现并确保已有及预置测试通过；不要改造 Aider 工作流，也不要修改 vLLM。

基线提交、预置测试内容的提交、Aider 版本、模型 ID、vLLM 参数、任务结果和实际测试命令必须写入该次实验记录，保证可复现。Aider 输出不得直接并入产品分支；产品实现仍需独立审查并由本项目的测试决定是否接受。

## 隔离与执行约束

- 临时仓库基于固定 commit；Aider 安装在独立 venv，不改共享的 vLLM venv。
- 使用 `/tmp` 下唯一实验目录和事先确认未占用的 loopback 端口；OTel receiver 也只绑定 loopback。实验前再次只读检查 GPU、端口和服务状态。
- 仅在 GPU 可用且不会中断他人任务时启动独立 vLLM 0.29.0 服务；若 GPU 仍被占用，停止 M4 执行并保留本文件状态，不复用或接管其它服务。
- Aider 使用 vLLM 暴露的 OpenAI-compatible endpoint。按官方 Aider 文档设置临时 `OPENAI_API_BASE` 和仅供本地 endpoint 的占位 `OPENAI_API_KEY`，模型名使用服务 `/v1/models` 返回的 ID 并加 `openai/` 前缀；具体参数以本机锁定的 Aider 版本 `--help` 核对。参考：[Aider OpenAI-compatible API](https://aider.chat/docs/llms/openai-compat.html)。
- 使用一次固定 prompt 和一次任务运行；不通过改任务、换模型或多次重试挑选成功结果。无论成功或失败，都记录 Aider 退出状态和测试结果。
- 原始 OTLP trace、日志和工作仓库只留在本次临时目录；分析时核对 request ID、token、时间及可用延迟，并仅把必要白名单字段写入脱敏 fixture。核对完成后清理临时数据，不保存 prompt、模型回复或密钥。

## 判定

通过要求：Aider 在该次固定任务中产出修改；接受测试和原有测试通过；服务导出的真实 `llm_request` trace 能与适配后的请求 ID、token、时间及已提供延迟逐项核对；报告中的 Cache/Scheduler 结论不超出实际观测，未提供字段保持 UNKNOWN。若 Aider 未完成任务或环境无法运行，只报告失败/阻塞及原始证据，不宣称 M4 通过。
