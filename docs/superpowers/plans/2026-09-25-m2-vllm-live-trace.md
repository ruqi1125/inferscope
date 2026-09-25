# M2 vLLM 真实 Trace 实施计划

> **给执行者：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项执行。步骤使用 `- [ ]` 语法跟踪。

**目标：** 以一条真实 vLLM 0.29.0 请求为输入，保存脱敏 OTLP fixture，核对原始 span 与 InferScope 事件，并验证 Adapter/CLI 回归，完成 M2。

**架构：** InferScope 产品仍保持零运行时依赖。远端测试接收器复用现有 gRPC/OTel protobuf 包，将一个或多个 OTLP 导出批次汇总为临时 JSON；只将白名单字段写入版本化 fixture，再由现有 Adapter 和 CLI 分析。长期目标仍是基于可追溯的真实 Serving Runtime 数据解释一次现成 Aider 编码任务的请求、延迟及 Cache/Scheduler 行为：M2 打通数据链路，M3 补证 Cache/Scheduler，M4 才做 Aider 真实验收。

**技术栈：** Python 3.10+、vLLM 0.29.0、远端已安装的 `grpcio`/`opentelemetry-proto`/protobuf、OTLP JSON、InferScope 当前 vLLM Adapter/CLI、pytest。

**规格：** `docs/superpowers/specs/2026-09-25-m2-vllm-live-trace-design.md`

## 全局约束

- `不增加 InferScope 产品依赖，不引入 Docker、Jaeger 或常驻服务。`
- `不得停止、重启或复用当前占用的 8000 端口服务。`
- `若没有可直接使用的模型权重，停止并征求用户同意，不自行下载。`
- `只发一个合成请求；这是采集与解析 smoke test，不是性能测试或 Agent 评测。`
- `优先只开启基础 OTel trace；仅当真实 span 缺少 M2 验收所必需的数据时，才评估是否需要 detailed traces，并先记录其开销及启用理由。不得执行压测或重复请求。`
- `fixture 和日志不得包含凭证、主机私有路径、真实用户输入或无关资源属性。`
- `对源 trace 中没有的阶段、KV、Prefix Cache、Scheduler 数据必须保持 UNKNOWN，不得合成观测值。`
- 提交信息使用中文；只有真实采集和 fixture/CLI 回归均通过才可把 M2 标记为完成。

## 重点审查项

1. 同一请求跨多个 OTLP `Export` RPC：接收器必须累加所有 `resourceSpans`，不能被后续批次覆盖；通过接收器的双批次测试验证。
2. protobuf JSON 的字段大小写和类型化 `attributes[].value`：真实 fixture 必须验证 `llm_request`、token 数和秒单位小数延迟可解析。
3. 带父 span 的请求 span：若真实 span 有父标识，fixture 中将其匿名化为固定占位标识并验证仍映射为请求；无父 span 时由现有 `tests/test_adapters.py` 父 span 回归覆盖。
4. 缺失 latency/生命周期边界：不得伪造数值；通过删除 fixture 中的 latency 属性再跑 Adapter，断言不可推导阶段为 `None/UNKNOWN`。
5. Prompt、主机路径和资源属性泄漏：fixture 仅保留白名单字段，将 request id 替换成 `req-vllm-029-001`，并由自动化测试检查。

---

### Task 1: 只读预检并选择既有模型

**文件：** 无。

**输入/产出：** 使用远端现有 `.venv-vllm29` 和用户指出的 `models` 目录；确认 vLLM/OTel 依赖、合适的既有模型、GPU 状态和两个未占用端口。此任务不得改代码、下载文件或改变服务状态。

- [x] **步骤 1：核对环境版本和接收器导入路径**

在远端既有 `.venv-vllm29` 中运行：

```bash
python -c "import grpc; from importlib.metadata import version; from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc; print('vllm', version('vllm'), 'grpcio', version('grpcio'), 'opentelemetry-proto', version('opentelemetry-proto'))"
```

预期：vLLM 为 `0.29.0`，两个 OTLP 模块可导入，并能输出已安装包版本。若导入失败，不安装任何依赖，记录缺少的包并暂停。

- [x] **步骤 2：只读检查模型、GPU 和端口**

在远端 InferScope 仓库目录运行 `find models -maxdepth 3 -type f -name config.json -print`，查看候选 `config.json`、权重分片总大小、`nvidia-smi` 输出及 `ss -ltnp`。结合现有启动参数，选择最小且 vLLM 支持、显存可容纳的既有模型；不读取权重文件内容。确认 8000 端口监听者不变，并选择另两个当前未占用端口（接收器和隔离的 vLLM 服务各一个）。

预期：能选出安全可用的现有权重和端口。若权重位于该目录以外，只检查用户指出的现有模型目录；若没有合适权重、GPU 显存不足或没有安全端口，则暂停并询问，不下载、不安装、不触碰既有服务。

### Task 2: 实现临时 OTLP 接收器并采集一次真实请求

**文件：**

- 新建：`scripts/vllm_otel_receiver.py`
- 新建：`scripts/test_vllm_otel_receiver.py`
- 临时输出：远端系统临时目录中的本次专属目录；原始 trace 和服务日志不进入仓库。

**接口：** 接收器提供 `main()`，命令行参数为 `--port`（默认 4317）、必填的 `--output`、`--timeout-seconds`（默认 300）；只绑定 `127.0.0.1`，不提供外部监听选项。类 `TraceReceiver` 实现 `Export(request, context)`，用 `MessageToDict(request, preserving_proto_field_name=False)` 转换 protobuf，在锁保护下累加每次请求的 `resourceSpans`，并返回 `trace_service_pb2.ExportTraceServiceResponse()`。`document()` 返回 `{"resourceSpans": [...]}`。接收器退出时写单个聚合 OTLP JSON；若未收到 span 则非零退出且不生成空文件。日志只记录监听地址和 span 数量，不记录属性或 prompt。

- [x] **步骤 1：实现独立接收器，不把它导入产品代码**

仅在脚本中导入 `grpc`、`google.protobuf.json_format.MessageToDict` 和 `opentelemetry.proto.collector.trace.v1.trace_service_pb2{,_grpc}`。使用两个 gRPC worker；实现 SIGINT/SIGTERM 退出和有界超时；退出后再写 JSON。脚本不得被 `src/inferscope` 导入，也不得增加 `pyproject.toml` 依赖。

- [x] **步骤 2：检查语法及命令行帮助**

在远端 vLLM 环境运行：

```bash
python -m py_compile scripts/vllm_otel_receiver.py
python scripts/vllm_otel_receiver.py --help
```

预期：两条命令成功；`--help` 不绑定端口。

- [x] **步骤 3：验证多批次聚合**

在 `scripts/test_vllm_otel_receiver.py` 中建立两个 `ExportTraceServiceRequest`：第一批包含名为 `llm_request` 的 span，第二批包含名为 `worker` 的 span。调用同一 `TraceReceiver` 两次后断言文档中有两个 `resourceSpans`，span 名按顺序为 `llm_request`、`worker`。测试代码：

```python
import unittest

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from vllm_otel_receiver import TraceReceiver

class ReceiverBatchTests(unittest.TestCase):
    def test_multiple_exports_accumulate(self):
        receiver = TraceReceiver()
        first = trace_service_pb2.ExportTraceServiceRequest()
        first_span = first.resource_spans.add().scope_spans.add().spans.add()
        first_span.name = "llm_request"
        second = trace_service_pb2.ExportTraceServiceRequest()
        second_span = second.resource_spans.add().scope_spans.add().spans.add()
        second_span.name = "worker"
        receiver.Export(first, None)
        receiver.Export(second, None)
        spans = [
            resource["scopeSpans"][0]["spans"][0]["name"]
            for resource in receiver.document()["resourceSpans"]
        ]
        self.assertEqual(spans, ["llm_request", "worker"])

if __name__ == "__main__":
    unittest.main()
```

运行 `python scripts/test_vllm_otel_receiver.py -v`；预期通过，且不监听端口、不加载模型。

- [x] **步骤 4：启动专属进程并只发一条合成请求**

启动前用 `TEMP_DIR="$(mktemp -d)"` 创建并记录唯一本次临时目录，只在其中写本次 receiver 输出、PID 和服务日志。使用任务 1 选出的接收器端口和模型路径，在 loopback 启动接收器；参考远端现有 vLLM 启动参数，但只把模型、服务端口和 `--otlp-traces-endpoint` 指向本次配置。不得复用现有服务端口、PID 文件、日志目标或清理命令。默认不启用 `--collect-detailed-traces`；仅在基础 span 缺少 M2 必需字段时，先记录缺少字段和已核对的开销，再决定是否启用。

在隔离服务上只发送一次固定请求：`Please respond with exactly: INFER_SCOPE_SMOKE_OK.`，输出 token 上限设为 8。先从隔离服务的只读 `/v1/models` 响应取模型 id，再向 `/v1/completions` 发送这唯一一次生成请求。收到响应后，只停止本次启动的 vLLM/接收器 PID，确认原先 8000 监听进程未变化。预期：原始临时 OTLP JSON 至少含一个 `llm_request`，request id、起止纳秒时间、token 数以及实际提供的 latency 属性均可读。

两个本次进程停止后，从原始文件中提取 request id（不打印其它属性）：

```bash
RAW_REQUEST_ID="$(python -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); s=next(s for r in d["resourceSpans"] for g in r.get("scopeSpans", []) for s in g.get("spans", []) if s.get("name") == "llm_request"); a={x["key"]:next(iter(x["value"].values())) for x in s.get("attributes", [])}; print(a["gen_ai.request.id"])' "$TEMP_DIR/raw-otel.json")"
PYTHONPATH=src python -m inferscope adapt vllm "$TEMP_DIR/raw-otel.json" --output "$TEMP_DIR/trace.jsonl"
PYTHONPATH=src python -m inferscope summary "$TEMP_DIR/trace.jsonl" --json
PYTHONPATH=src python -m inferscope inspect "$TEMP_DIR/trace.jsonl" "$RAW_REQUEST_ID" --json
```

逐字段将原始 `llm_request` 的 request id、起止时间、token 数和已有 latency 属性与事件/摘要比较；临时报告不得复制到仓库。若服务不能安全启动、没有导出请求 span 或无法逐字段核对，暂停报告，不重发请求。

### Task 3: 生成脱敏 fixture 并增加源数据回归

**文件：**

- 新建：`tests/fixtures/vllm-0.29.0-otel.json`
- 新建：`tests/test_vllm_live_fixture.py`
- 对照：`src/inferscope/adapters/vllm/adapter.py`、`src/inferscope/adapters/base.py`、`src/inferscope/analyzers/request.py`

**接口：** 测试将 fixture 用 `json.loads(..., parse_float=Decimal)` 读取，选出唯一 `llm_request`，解码其 OTel typed attributes，再交给 `VLLMAdapter().to_events(document)` 和 `summarize_requests(events)`。fixture 中 request id 固定为 `req-vllm-029-001`。

- [x] **步骤 1：从一次真实采集制作最小白名单 fixture**

保留 OTLP 的 `resourceSpans/scopeSpans/spans` 外壳，以及唯一 `llm_request` 的 name、起止 Unix 纳秒、request id、实际存在的 prompt/completion token 数和 latency 属性。如果有 `parentSpanId`，改写为 `parent-span-001`；没有则不添加。request id 改写为 `req-vllm-029-001`。移除 trace/span id、prompt/content 属性、所有 resource 属性、主机名、路径和无关 span。逐字段与原始临时采集核对后，只删除本次确切的临时原始文件和日志，再对临时目录执行非递归删除；若目录不为空则保留并报告，不递归清理。

- [x] **步骤 2：先写并运行真实 span 到事件的回归**

在 `tests/test_vllm_live_fixture.py` 加辅助函数 `_typed_attributes(attributes)`，按 OTLP 列表中每项的 `key` 取 `value` 唯一 oneof 值；然后加入下列断言结构：

```python
import json
import copy
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from inferscope.adapters.base import iter_spans
from inferscope.adapters.vllm.adapter import VLLMAdapter
from inferscope.analyzers.request import summarize_requests
from inferscope.core.time import seconds_to_nanoseconds

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vllm-0.29.0-otel.json"

def _typed_attributes(attributes):
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in attributes
    }

def _load_fixture():
    document = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    request_spans = [span for span in iter_spans(document) if span["name"] == "llm_request"]
    assert len(request_spans) == 1
    return document, request_spans[0]

LATENCIES = {
    "queue_ns": "gen_ai.latency.time_in_queue",
    "prefill_ns": "gen_ai.latency.time_in_model_prefill",
    "decode_ns": "gen_ai.latency.time_in_model_decode",
    "ttft_ns": "gen_ai.latency.time_to_first_token",
    "e2e_ns": "gen_ai.latency.e2e",
}
def test_real_vllm_request_matches_raw_span():
    document, raw = _load_fixture()
    raw_attrs = _typed_attributes(raw["attributes"])
    events = VLLMAdapter().to_events(document)
    rows = summarize_requests(events)
    arrived = next(event for event in events if event.event_type == "REQUEST_ARRIVED")
    finished = next(event for event in events if event.event_type == "REQUEST_FINISHED")

    assert len(rows) == 1
    assert rows[0].request_id == raw_attrs["gen_ai.request.id"] == "req-vllm-029-001"
    assert arrived.timestamp_ns == int(raw["startTimeUnixNano"])
    assert finished.timestamp_ns == int(raw["endTimeUnixNano"])
    for metric, attribute in LATENCIES.items():
        if attribute in raw_attrs:
            expected = seconds_to_nanoseconds(Decimal(str(raw_attrs[attribute])), "duration")
            assert finished.attributes[metric] == expected
            assert rows[0].latency_sources[metric] == "OBSERVED"
        else:
            assert rows[0].latency_sources[metric] != "OBSERVED"

    token_attributes = {
        "input_tokens": "gen_ai.usage.prompt_tokens",
        "output_tokens": "gen_ai.usage.completion_tokens",
    }
    for field, attribute in token_attributes.items():
        expected = int(raw_attrs[attribute]) if attribute in raw_attrs else None
        assert getattr(rows[0], field) == expected
```

另断言 fixture 全部 span attribute 名都属于显式白名单，且 `resourceSpans` 不带 `resource.attributes`。运行 `pytest tests/test_vllm_live_fixture.py -q`。若测试暴露真实格式与 Adapter 不符，保留失败断言后只做最小、有原始证据支持的修复；不添加猜测性 alias。

- [x] **步骤 3：验证缺失 latency 不会被伪造成观测值**

基于 fixture 深拷贝 document，移除 span 中所有 `gen_ai.latency.*` 属性，再运行当前 Adapter。单独测试需再次加载 fixture，不依赖另一测试的局部变量：

```python
def test_missing_latency_fields_are_not_fabricated():
    document, raw = _load_fixture()
    without_latency = copy.deepcopy(document)
    for span in iter_spans(without_latency):
        span["attributes"] = [
            item for item in span["attributes"]
            if not item["key"].startswith("gen_ai.latency.")
        ]
    summary = summarize_requests(VLLMAdapter().to_events(without_latency))[0]
    for field in ("queue_ns", "prefill_ns", "decode_ns", "ttft_ns"):
        assert getattr(summary, field) is None
        assert summary.latency_sources[field] == "UNKNOWN"
    assert summary.e2e_ns == int(raw["endTimeUnixNano"]) - int(raw["startTimeUnixNano"])
    assert summary.latency_sources["e2e_ns"] == "DERIVED"
```

该测试不改动已提交 fixture。

- [x] **步骤 4：验证真实 fixture 的 CLI 三段链路**

在测试中用 pytest 的 `tmp_path` 和 `subprocess.run([...])` 参数数组，不使用 shell 字符串，依次调用 `adapt`、`summary`、`inspect`。测试命令构造与验收至少包含：

```python
def test_real_vllm_fixture_flows_through_cli(tmp_path):
    _, raw = _load_fixture()
    trace_path = tmp_path / "trace.jsonl"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    adapt = subprocess.run(
        [sys.executable, "-m", "inferscope", "adapt", "vllm", str(FIXTURE), "--output", str(trace_path)],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    assert adapt.returncode == 0, adapt.stderr
    summary_result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path), "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    inspect_result = subprocess.run(
        [sys.executable, "-m", "inferscope", "inspect", str(trace_path), "req-vllm-029-001", "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    assert summary_result.returncode == 0, summary_result.stderr
    assert inspect_result.returncode == 0, inspect_result.stderr
    summary = json.loads(summary_result.stdout)["requests_detail"][0]
    inspected = json.loads(inspect_result.stdout)["request"]
    assert summary["request_id"] == inspected["request_id"] == "req-vllm-029-001"
    assert summary["arrived_ns"] == int(raw["startTimeUnixNano"])
    assert summary["finished_ns"] == int(raw["endTimeUnixNano"])
```

另断言 summary 的 token/latency 与 fixture 相符。运行：

```bash
pytest tests/test_vllm_live_fixture.py tests/test_adapters.py tests/test_analyzer.py -q
```

预期：新增测试和现有 parent-span、缺失边界测试都通过。源数据没有的 KV/Prefix Cache/Scheduler 字段不加到 fixture。

### Task 4: 文档、全量基本测试和 M2 结项

**文件：**

- 修改：`docs/adapters.md`
- 修改：`docs/implementation-plan.md`
- 对照：`docs/trace-format.md`

**接口：** Adapter 文档提供已验证的 vLLM 0.29.0 loopback 接收器调用、单请求 smoke、fixture 和 CLI 回归方式；唯一权威路线只在有真实采集与测试证据后标记 M2 完成，M3/M4 顺序不变。

- [x] **步骤 1：记录已验证的用法和边界**

在 `docs/adapters.md` 的 vLLM 小节记录接收器命令、经实测确认的 endpoint 写法、fixture 路径和 Adapter/CLI 检查命令。说明这是单请求 smoke，不是基准测试或 Agent 测试；未由源 span 提供的字段仍为 `UNKNOWN`，推导字段继续标记为 `DERIVED`。除非本次确实启用 detailed traces，否则不得写成已启用。

- [x] **步骤 2：跑全量基本回归并复核 CLI JSON**

运行：

```bash
pytest -q
```

预期：全套测试通过。再次读取三条 fixture CLI JSON，并与 fixture 对照。运行 `git diff --check`，确认工作区只涉及接收器及其测试、fixture、新回归测试和两份文档。若手工 CLI 使用临时文件，先验证临时目录的规范绝对路径位于系统临时目录，再仅删除本次创建的 JSON 文件并用 `rmdir` 删除空目录；不递归删除目录。

- [x] **步骤 3：仅凭实测记录 M2 完成**

只修改 `docs/implementation-plan.md` 的 M2 状态/依据行，记下 vLLM 版本、一次真实请求、fixture 路径、实际 pytest 通过数及 CLI 检查项。保持 M3 和 M4 状态不变。若 M2 任一验收失败，M2 继续标记未完成，并记录缺少的具体证据。

- [x] **步骤 4：用中文提交已验证产物**

```bash
git add scripts/vllm_otel_receiver.py scripts/test_vllm_otel_receiver.py src/inferscope/adapters/vllm/adapter.py tests/fixtures/vllm-0.29.0-otel.json tests/test_vllm_live_fixture.py docs/adapters.md docs/implementation-plan.md
git commit -m "打通 vLLM 真实 Trace 验收链路"
```

## M2 之后的顺序

M2 的最后一步就是单请求真实 smoke 加 fixture/CLI 回归，不再另做 Aider 测试。下一阶段按路线进入 M3，补充有证据的 vLLM Cache/Scheduler 观测。只有 M2 通过且 M3 中未支持字段如实标为 `UNKNOWN` 后，才到 M4 使用原样的现成 Aider 做固定编码任务验收；不跳过 M3，不重设计 Agent 工作流。
