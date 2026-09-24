# InferScope

InferScope 是一个轻量级的 LLM Serving Runtime 分析工具，用来理解一次推理请求在 Serving 系统中的生命周期、前缀缓存复用和延迟表现。它提供统一事件流、JSONL trace/workload、离线 cache replay 和 CLI 分析能力，并计划通过 Adapter 接入 vLLM 与 SGLang。

项目不替代 Nsight、Prometheus 或 Grafana，也不实现推理引擎。它聚焦回答：**为什么这次 LLM 请求表现成这样？**

## 当前能力

- `summary`：汇总 trace 请求和延迟阶段。
- `inspect`：查看单个请求的生命周期和缓存信息。
- `replay`：模拟 workload 的 prefix cache 复用。
- `compare`：对相同 workload 比较 hash-block 与 radix cache。
- JSONL 输入校验，错误定位到文件和行号。

## 安装

```bash
python -m pip install -e .
```

## 快速开始

```bash
inferscope replay examples/demo.jsonl --cache radix --block-size 4 --capacity-blocks 128
inferscope compare examples/demo.jsonl --block-size 4 --capacity-blocks 128
inferscope summary examples/demo-trace.jsonl
inferscope inspect examples/demo-trace.jsonl req-001
```

工作负载每行包含 `request_id`、相对秒数 `timestamp`、整数数组 `input_token_ids` 和 `output_tokens`。Trace 每行包含 `timestamp_ns`、`event_type` 与 `request_id`。详见 [trace 格式](docs/trace-format.md)。

## 路线与边界

完整架构与所有实施阶段见 [架构路线](docs/architecture.md)，本轮范围和关键语义见 [实施计划](docs/implementation-plan.md)。线上 adapter 只做可观测数据映射；未知事实保持 unknown，不做无依据归因。

## 开发

```bash
python -m pip install -e '.[dev]'
pytest
```
