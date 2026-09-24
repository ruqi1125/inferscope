# M2 vLLM 真实 Trace 验收设计

## 目标与范围

在既定路线 `docs/implementation-plan.md` 的 M2 范围内，以远端已安装的 vLLM 0.29.0 做一次最小真实链路验收：运行中的服务导出 OTLP trace，InferScope vLLM Adapter 转换为 v1 Trace JSONL，现有 CLI 可分析结果，并将脱敏后的真实 trace 留作回归 fixture。只发一个合成请求；这是采集与解析 smoke test，不是性能测试或 Agent 评测。M4 的 Aider 验收不提前开展。

## 方案

1. 复用远端 `.venv-vllm29` 中已有的 `grpcio`、`opentelemetry-proto` 和 protobuf 实现仅供验收使用的 OTLP gRPC 接收器。接收器监听 loopback 上经检查确认空闲的临时端口，将收到的 span 批次序列化为 Adapter 已支持的 OTLP JSON 形状；不增加 InferScope 产品依赖，不引入 Docker、Jaeger 或常驻服务。
2. vLLM 0.29.0 指向该接收器，在另一个经检查确认空闲的端口启动；不得停止、重启或复用当前占用的 8000 端口服务。先检查已有模型缓存和远端既有模型目录。若没有可直接使用的模型权重，停止并征求用户同意，不自行下载。
3. 使用固定的合成 prompt 和单次生成请求。优先只开启基础 OTel trace；仅当真实 span 缺少 M2 验收所必需的数据时，才评估是否需要 detailed traces，并先记录其开销及启用理由。不得执行压测或重复请求。
4. 将原始采集输出保存在临时目录；核验后只把最小必要、无敏感内容的 span 保存为版本化 fixture。fixture 和日志不得包含凭证、主机私有路径、真实用户输入或无关资源属性。运行 `inferscope adapt vllm`，再用 `summary`、`inspect` 检查转换结果。
5. 加入 fixture 回归测试，断言真实 request span 的 request id、到达/完成时间、token 数和源数据中存在的 latency 值可被解析；将 CLI 转换及分析纳入基本验证。对源 trace 中没有的阶段、KV、Prefix Cache、Scheduler 数据必须保持 `UNKNOWN`，不得合成观测值。同步更新 Adapter 使用说明和 M2 验收记录。

## 验收标准

- 一次真实 vLLM 0.29.0 请求成功把 OTLP spans 发到临时接收器，且现有 8000 端口服务未被改动。
- 脱敏 fixture 能由标准 `pytest` 回归；Adapter 与 `adapt`、`summary`、`inspect` 的基本链路可复现。
- request id、span 边界时间、源中存在的 token 与 latency 值逐项对照原始 span；转换器生成的推导字段继续标记为 `DERIVED`，缺失观测明确保持 `UNKNOWN`。
- 不要求新增产品运行依赖、不要求性能指标、不下载模型、不做 Aider/Agent 工作流或额外 Runtime 字段接入。
- smoke test 结束后仅清理本次启动的确切进程和临时数据，不触碰其他用户数据或服务。

## 假设与停止条件

- 已知 vLLM 0.29.0 环境含验收接收器所需的 OTel gRPC/protobuf 包；执行前仍需在远端验证具体导入路径及版本。
- 先做只读模型缓存/目录和端口检查。找不到可用权重、无法安全避开既有服务端口，或需新增下载/安装/重启等未获同意操作时，停止并报告，不扩大权限或改动环境。
- 版本化 fixture 只收录一条请求所需的最小 spans；原始临时采集文件不纳入仓库。
- 本设计只细化 M2 的实现/验收方法，不修改 `docs/implementation-plan.md` 的阶段、目标、Agent 选择或依赖顺序。
