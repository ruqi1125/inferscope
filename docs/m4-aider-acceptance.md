# M4：Aider + vLLM 固定验收

本文件细化 `docs/implementation-plan.md` 的 M4，不改变框架选择或阶段顺序。Aider 按原生交互方式使用；InferScope 只分析其请求经过 vLLM 时的真实 trace，不自建 Agent 工作流。

## 当前状态

M4 尚未通过。2026-09-26 在用户确认资源空闲后，按固定任务进行了单次实测：选用专用 `/home/nas511/zhangruqi/.venv-vllm29`（vLLM 0.29.0）、既有 Llama-3-8B-Instruct、GPU 0、loopback 端口 18000/4317；启动时将该 venv 的 `bin` 加入 PATH，并按仓库 CUDA 12.0 兼容说明设置 `VLLM_USE_FLASHINFER_SAMPLER=0`。服务成功加载模型，GPU 在结束后恢复空闲。

固定编码任务以基线 `985c1e2abaffda333fdca2854231e3c793b87503` 临时检出；接受测试来自 `7ade70e`（blob `6e1c7dc64118b5b358060baca8c8339766246a8d`），预置提交为 `dbccdd8776e12cf87303d00325f1ced06ddda7b5`（`预置 M4 接受测试`）。vLLM 启动命令参数为 `--host 127.0.0.1 --port 18000 --served-model-name inferscope-m4-llama-3-8b --max-model-len 4096 --max-num-seqs 1 --gpu-memory-utilization 0.85 --otlp-traces-endpoint grpc://127.0.0.1:4317`，并设置 `CUDA_VISIBLE_DEVICES=0`、`OTEL_EXPORTER_OTLP_TRACES_INSECURE=true`。Aider 在独立 venv 安装时从 0.86.0 自更新到 0.86.2；固定 prompt 的 SHA-256 为 `4c0c121898b0bcfb4956f289f5316ec7a6bd59bc71d25028a973f6d04f6bbc17`（prompt 不留存）。唯一一次实际任务使用 `--model openai/inferscope-m4-llama-3-8b --message-file <固定任务文件> --yes-always --no-check-update --no-auto-commits --auto-test --test-cmd 'pytest -q' --analytics-disable`。Aider 能访问 `/v1/models` 并完成仓库映射，但随后进程持续尝试连接 `huggingface.co:443`，该 HTTPS 连接超时；约 8 分钟仍未发出模型 completion 请求，按固定失败规则终止，退出码 143。vLLM 日志中 `/v1/chat/completions` 请求数为 0；OTLP 接收器收到 45 条 vLLM 启动/加载 span，但 `llm_request` 数为 0。因此 Aider 没有产出修改，`--auto-test` 未执行，不能评价任务效果或跨来源请求关联。另在基线临时仓库执行接受测试命令 `pytest -q`，收集阶段因待实现的 `inferscope.runtime.correlation` 模块缺失而报错；这是预期的任务缺口，不是 Aider 结果。

随后按用户建议验证 HF 镜像。远端直连 `huggingface.co` 超时，`https://hf-mirror.com` 返回 HTTP 200；设置 `HF_ENDPOINT=https://hf-mirror.com` 后，HF Hub API 查询成功。Aider 在隔离的临时 HF 缓存中取回 `Xenova/llama-3-tokenizer`（9,084,490 字节）；既有 Llama 模型目录本来就有完整权重和 tokenizer，因此没有下载权重，也没有改动模型目录。镜像解决了初始化联网阻塞。

镜像后的任务仍未完成。Aider 0.86.2 使用同一固定 prompt（SHA-256 不变）、基线 `985c1e2abaffda333fdca2854231e3c793b87503`、接受测试 blob `6e1c7dc64118b5b358060baca8c8339766246a8d` 和模型 ID `inferscope-m4-llama-3-8b`；本次临时接受测试预置提交为 `8d6c2de5091165df613e4174d4792191a50f14c3`。`--yes-always` 按 Aider 原生流程自动采纳模型建议并把相关源文件加入上下文。vLLM 先按 `--max-model-len 4096` 运行：两次诊断运行分别出现一次 200 completion 后跟随一次 400；Aider 报告输入约 6,379/0、6,095/3,072。随后核对既有 `config.json` 的 `max_position_embeddings=8192`，将最终验收尝试的唯一 Serving 参数调整为 `--max-model-len 8192`（其余参数不变），Aider 元数据设为 7,168 输入/1,024 输出。最终 Aider 命令仍为固定模型与固定任务，并使用 `--model-metadata-file <临时元数据文件> --yes-always --no-check-update --no-auto-commits --auto-test --test-cmd 'pytest -q' --analytics-disable`；进程环境设置 `OPENAI_API_BASE=http://127.0.0.1:18000/v1`、本地占位 `OPENAI_API_KEY`、`HF_ENDPOINT=https://hf-mirror.com` 和隔离 `HF_HOME`。Aider 前两次 completion 成功，但自动加入文件后估算输入 8,823/7,168，继续发送后被 vLLM 400 拒绝。Aider 退出码为 0，但临时仓库只有 Aider 自动生成的 `.gitignore` 变化，没有产品文件修改；`--auto-test --test-cmd 'pytest -q'` 未触发。故任务效果和测试结果仍不能判为通过。该证据将问题从 HF 网络定位为：固定 Llama 3 8B 的 8,192 上下文不足以容纳 Aider 此次原生文件发现与编辑上下文。

临时 trace 中实际成功的四条 `llm_request` 白名单字段如下；三次被 vLLM 拒绝的 HTTP 400 不生成成功请求 span：

| Serving max len | Request ID | Prompt/Completion tokens | TTFT/E2E（秒） |
|---:|---|---:|---:|
| 4096 | `chatcmpl-a2c898226a5579c3` | 2059 / 71 | 0.781 / 2.183 |
| 4096 | `chatcmpl-861c70e39670319d` | 2008 / 88 | 0.301 / 2.045 |
| 8192 | `chatcmpl-885eadd76f214cd3` | 2877 / 104 | 0.785 / 2.894 |
| 8192 | `chatcmpl-87381f3ddf4a39d0` | 5613 / 95 | 1.238 / 3.210 |

所有接收器、vLLM/Aider 进程、临时 HF 缓存、固定 prompt、原始 trace 和实验仓库均已清理；GPU/端口已释放，未保留模型回复或密钥。M4 仍未通过。继续前需决定如何在保持模型与任务不变时压缩 Aider 原生上下文（如调整其内置 repo-map/编辑上下文参数）；当前无证据支持修改 InferScope 产品代码。

同日对当前代码执行 `python -m pytest -q`：105 passed，3 subtests passed。另用随仓库保存的 vLLM 0.29.0 脱敏 OTel trace 与 native-stats JSONL 实际运行 `adapt → summary`：6 个事件形成 1 个请求，五项延迟均标为 `OBSERVED`；trace 请求与 native 统计属于不同采集轮次，关联报告为 0 matched、各自 unmatched，逐请求缓存值保持 `UNKNOWN`，engine 级 Scheduler/Prefix Cache/KV 淘汰仍单独报告。该检查验证的是已保存样本的 CLI 行为，不是新的 live inference 或 Aider 验收，不能代替 M4。

应用户授权，2026-09-26 准备改用已有 `/home/nas511/zhangruqi/models/Qwen3.6-27B-GPTQ-Int4`，保持固定任务原文和预置接受测试。模型目录已有权重及完整 tokenizer；`config.json` 为 `model_type=qwen3_5`、架构 `Qwen3_5ForConditionalGeneration`、上下文上限 262,144；现有 vLLM 0.29.0 安装包含该架构实现。本次没有下载模型权重或 tokenizer。独立 Aider 0.86.2/pytest 环境已在 `/tmp` 创建；在基线 `985c1e2abaffda333fdca2854231e3c793b87503` 预置的测试 blob 与记录一致，基线测试按预期因尚无 `inferscope.runtime.correlation` 而收集失败。

GPU 预检在 14:53 时两卡空闲；准备环境期间，另一项训练任务于 15:07 开始占用两卡。本次没有在服务启动前重新检查，15:11 启动 vLLM 时便因显存不足退出：CUDA 1 仅剩 8.66/23.56 GiB，低于 `--gpu-memory-utilization 0.85` 对应的 20.02 GiB 启动预算。vLLM 退出码为 1；loopback 接收器仅收到 10 条初始化 span，`llm_request` 为 0。没有启动 Aider、没有真实 completion、`--auto-test` 未执行；未触碰或中断其他用户任务。服务与接收器均已停止，端口释放，包含固定 prompt、原始 trace、日志和虚拟环境在内的本次 `/tmp` 实验目录已清理。M4 仍未通过；本次没有产生产品代码或新的模型数据证据。下次须在准备完成后、紧邻 vLLM 启动前重新确认 GPU 和端口；若 GPU 被占用则停止，不得降低预算与其他任务争抢资源。

2026-09-26 用户确认资源空闲后再次实测同一固定任务，改用上述 Qwen。启动前 GPU 0/1 各空闲 24,124 MiB、无计算进程，18000/4317 均未占用；vLLM 0.29.0 以 `CUDA_VISIBLE_DEVICES=0,1`、`--tensor-parallel-size 2 --max-model-len 32768 --max-num-seqs 1 --gpu-memory-utilization 0.85` 启动，loopback OTel endpoint 为 `127.0.0.1:4317`。模型从 NAS 读取约 19.54 GiB 权重并完成初始化，KV cache 36,864 tokens，`/v1/models` 返回 `inferscope-m4-qwen35-27b-gptq`。Aider 0.86.2 使用隔离 venv、HF 镜像和本地占位 API key；运行时 PATH 临时优先使用已有 `/home/nas511/zhangruqi/agent-235/.venv/bin` 提供 pytest，执行原定 `--yes-always --no-check-update --no-auto-commits --auto-test --test-cmd 'pytest -q' --analytics-disable`；模型元数据上限为 28,672 输入、4,096 输出 tokens。

本次从规划文件原文提取的任务 SHA-256 为 `b81ca419ba3ae9c0b86f69b662128573228da3f375a9c3b1b3e11c6c29149941`，本地与远端文件一致；历史 Llama 记录的 SHA-256 是 `4c0c121898b0bcfb4956f289f5316ec7a6bd59bc71d25028a973f6d04f6bbc17`。两者不一致，因此不能声称本次字节级 prompt 哈希与历史运行相同；本次没有改写规划文件中的任务原文，历史差异留待下一轮验收前厘清。

vLLM access log 有 5 次 `/v1/chat/completions` HTTP 200，接收器实际保存 165 spans，其中有 4 条 `llm_request`。按脱敏白名单核对的真实请求为：

| Request ID | Prompt/Completion tokens | TTFT/E2E（秒） |
|---|---:|---:|
| `chatcmpl-b72dbf16f868a770` | 7,661 / 1,537 | 6.152 / 29.822 |
| `chatcmpl-b6315540d013393f` | 16,474 / 13,966 | 24.164 / 340.126 |
| `chatcmpl-a905b357f5ca450f` | 20,538 / 10,500 | 26.238 / 220.016 |
| `chatcmpl-bf7c04fce243ed77` | 15,998 / 1,726 | 19.278 / 62.651 |

Aider 首先要求补入文件；`--yes-always` 自动选取文件后，Qwen 持续输出解释和整文件草稿，而不是 Aider 可识别的编辑格式。Aider 将解释片段误作文件名，在临时仓库生成伪文件并改写临时 `main.py`；自动 flake8 检查捕获 `SyntaxError`。随后上下文估算从 30,742 增至 45,160，超过配置的 28,672 输入上限，输出仍重复同类方案。为停止无效请求，本次 Aider 由 Ctrl+C 中断；wrapper 未保存退出码。Aider 没有形成有效产品实现，预置 `pytest -q` 未运行（只运行了自动 flake8 且失败）；基线接受测试仍仅有预期的缺失模块收集错误。Aider access log 与 OTel 分别记录 5 个成功 HTTP 请求和 4 条请求 span，数量差异也尚未解释。故这次证明模型服务和真实请求采集可以工作，但不证明 Agent 编码或 M4 验收通过。

本次 vLLM 和 receiver 已对各自 PID 发送 SIGTERM 并正常退出；确认两端口释放、两卡恢复为各 24,124 MiB 空闲。提取必要脱敏字段后已清理本次唯一 `/tmp/inferscope-m4-qwen.2ZwphT` 实验目录，未保留 prompt 文件、模型回复、原始 trace、日志或密钥。下一步不是原样重跑：先厘清固定 prompt 哈希历史差异，并解决 Qwen 输出与 Aider 编辑协议不兼容及输入上下文超过元数据预算的问题；再明确一次新的、可复现的验收配置。临时仓库中的 Aider 输出不得并入产品分支。

## 固定编码任务

以 `985c1e2`（本次 M3 开发开始前的 InferScope 基线）为临时仓库基线。在临时 checkout 中预置本项目 `tests/test_runtime_trace_correlation.py` 作为接受测试，然后把以下任务原文交给 Aider：

> 为 InferScope 的 `summary` 命令添加可选的 vLLM 原生统计关联能力，支持重复指定 `--runtime-stats FILE` 以读取多个 engine 的 JSONL。只按 trace 与 native 统计中的精确 request ID 关联；仅当两侧均唯一且缓存 token 来源为 OBSERVED 时，才将逐请求 cached tokens 与 fraction 加入 summary。缺失、重复回调、跨 engine 同 ID 冲突或统计未知时必须保留 UNKNOWN，并提供关联计数。服务级 Scheduler、Prefix Cache 与 KV 淘汰数据必须继续以 ENGINE scope 单独报告，绝不能归因到单请求。保留现有不带新参数时的 CLI JSON 契约，不增加运行时依赖。完成实现并确保已有及预置测试通过；不要改造 Aider 工作流，也不要修改 vLLM。

基线提交、预置测试内容的提交、Aider 版本、模型 ID、vLLM 参数、任务结果和实际测试命令必须写入该次实验记录，保证可复现。Aider 输出不得直接并入产品分支；产品实现仍需独立审查并由本项目的测试决定是否接受。

## 隔离与执行约束

- 临时仓库基于固定 commit；Aider 安装在独立 venv，不改共享的 vLLM venv。
- 使用 `/tmp` 下唯一实验目录和事先确认未占用的 loopback 端口；OTel receiver 也只绑定 loopback。实验前再次只读检查 GPU、端口和服务状态。
- 准备环境可能耗时；GPU/端口检查必须紧邻 vLLM 启动前执行。若检查后经历准备工作或等待，应重新检查；发现他人任务占用 GPU 或显存预算不足时立即停止，不接管、不终止、不与其争抢资源。
- 仅在 GPU 可用且不会中断他人任务时启动独立 vLLM 0.29.0 服务；若 GPU 仍被占用，停止 M4 执行并保留本文件状态，不复用或接管其它服务。
- Aider 使用 vLLM 暴露的 OpenAI-compatible endpoint。按官方 Aider 文档设置临时 `OPENAI_API_BASE` 和仅供本地 endpoint 的占位 `OPENAI_API_KEY`，模型名使用服务 `/v1/models` 返回的 ID 并加 `openai/` 前缀；具体参数以本机锁定的 Aider 版本 `--help` 核对。参考：[Aider OpenAI-compatible API](https://aider.chat/docs/llms/openai-compat.html)。
- 使用一次固定 prompt 和一次任务运行；不通过改任务、换模型或多次重试挑选成功结果。无论成功或失败，都记录 Aider 退出状态和测试结果。
- 原始 OTLP trace、日志和工作仓库只留在本次临时目录；分析时核对 request ID、token、时间及可用延迟，并仅把必要白名单字段写入脱敏 fixture。核对完成后清理临时数据，不保存 prompt、模型回复或密钥。

## 判定

通过要求：Aider 在该次固定任务中产出修改；接受测试和原有测试通过；服务导出的真实 `llm_request` trace 能与适配后的请求 ID、token、时间及已提供延迟逐项核对；报告中的 Cache/Scheduler 结论不超出实际观测，未提供字段保持 UNKNOWN。若 Aider 未完成任务或环境无法运行，只报告失败/阻塞及原始证据，不宣称 M4 通过。
