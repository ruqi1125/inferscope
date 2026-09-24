import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
import unittest

import grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

from vllm_otel_receiver import TraceReceiver, _stop_server, run_receiver


class ReceiverBatchTests(unittest.TestCase):
    def test_multiple_exports_accumulate(self):
        receiver = TraceReceiver()
        first = trace_service_pb2.ExportTraceServiceRequest()
        first_span = first.resource_spans.add().scope_spans.add().spans.add()
        first_span.name = "llm_request"
        first_span.attributes.add(
            key="gen_ai.request.id"
        ).value.string_value = "raw-id"

        second = trace_service_pb2.ExportTraceServiceRequest()
        second_span = second.resource_spans.add().scope_spans.add().spans.add()
        second_span.name = "worker"

        first_response = receiver.Export(first, None)
        second_response = receiver.Export(second, None)
        document = receiver.document()

        spans = [
            resource["scopeSpans"][0]["spans"][0]
            for resource in document["resourceSpans"]
        ]
        self.assertEqual([span["name"] for span in spans], ["llm_request", "worker"])
        self.assertEqual(
            spans[0]["attributes"][0]["value"], {"stringValue": "raw-id"}
        )
        self.assertIsInstance(
            first_response, trace_service_pb2.ExportTraceServiceResponse
        )
        self.assertIsInstance(
            second_response, trace_service_pb2.ExportTraceServiceResponse
        )

    def test_grpc_service_accumulates_multiple_export_rpcs(self):
        receiver = TraceReceiver()
        executor = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(executor)
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
        port = server.add_insecure_port("127.0.0.1:0")
        self.assertNotEqual(port, 0)
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                grpc.channel_ready_future(channel).result(timeout=2)
                stub = trace_service_pb2_grpc.TraceServiceStub(channel)
                for name in ("llm_request", "worker"):
                    request = trace_service_pb2.ExportTraceServiceRequest()
                    span = (
                        request.resource_spans.add()
                        .scope_spans.add()
                        .spans.add()
                    )
                    span.name = name
                    stub.Export(request, timeout=2)
        finally:
            server.stop(grace=0).wait()
            executor.shutdown(wait=True, cancel_futures=True)

        spans = [
            resource["scopeSpans"][0]["spans"][0]["name"]
            for resource in receiver.document()["resourceSpans"]
        ]
        self.assertEqual(spans, ["llm_request", "worker"])

    def test_shutdown_waits_for_an_in_flight_export(self):
        export_started = Event()
        allow_export_to_finish = Event()

        class DelayedTraceReceiver(TraceReceiver):
            def Export(self, request, context):
                export_started.set()
                if not allow_export_to_finish.wait(timeout=3):
                    context.abort(
                        grpc.StatusCode.DEADLINE_EXCEEDED,
                        "test export was not released",
                    )
                return super().Export(request, context)

        receiver = DelayedTraceReceiver()
        server_executor = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(server_executor)
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
        port = server.add_insecure_port("127.0.0.1:0")
        self.assertNotEqual(port, 0)
        server.start()
        client_executor = ThreadPoolExecutor(max_workers=1)
        stop_thread = None
        stop_started = Event()
        stop_graces = []
        original_stop = server.stop

        def tracked_stop(grace):
            stopped = original_stop(grace)
            stop_graces.append(grace)
            stop_started.set()
            return stopped

        server.stop = tracked_stop
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                grpc.channel_ready_future(channel).result(timeout=2)
                stub = trace_service_pb2_grpc.TraceServiceStub(channel)
                request = trace_service_pb2.ExportTraceServiceRequest()
                request.resource_spans.add().scope_spans.add().spans.add().name = (
                    "llm_request"
                )
                export_future = client_executor.submit(
                    stub.Export, request, timeout=5
                )
                self.assertTrue(export_started.wait(timeout=2))

                stop_thread = Thread(target=_stop_server, args=(server, server_executor))
                stop_thread.start()
                self.assertTrue(stop_started.wait(timeout=1))
                allow_export_to_finish.set()
                stop_thread.join(timeout=3)
                self.assertFalse(stop_thread.is_alive())
                self.assertEqual(stop_graces, [2.0])
                export_future.result(timeout=2)
        finally:
            allow_export_to_finish.set()
            server.stop = original_stop
            server.stop(grace=2).wait()
            server_executor.shutdown(wait=True, cancel_futures=True)
            client_executor.shutdown(wait=True, cancel_futures=True)
            if stop_thread is not None:
                stop_thread.join(timeout=3)

        spans = [
            span
            for resource in receiver.document()["resourceSpans"]
            for scope in resource["scopeSpans"]
            for span in scope["spans"]
        ]
        self.assertEqual([span["name"] for span in spans], ["llm_request"])

    def test_cli_writes_exported_document_after_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.json"
            script = Path(__file__).with_name("vllm_otel_receiver.py")
            environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(script),
                    "--port",
                    "0",
                    "--output",
                    str(output),
                    "--timeout-seconds",
                    "10",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=environment,
            )
            lines = queue.Queue()
            reader = Thread(
                target=lambda: [lines.put(line) for line in process.stdout],
                daemon=True,
            )
            reader.start()
            try:
                ready_line = lines.get(timeout=5)
                match = re.search(r"127\.0\.0\.1:(\d+)", ready_line)
                self.assertIsNotNone(match, ready_line)
                self.assertFalse(output.exists())
                port = int(match.group(1))
                request = trace_service_pb2.ExportTraceServiceRequest()
                span = request.resource_spans.add().scope_spans.add().spans.add()
                span.name = "llm_request"
                with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                    grpc.channel_ready_future(channel).result(timeout=2)
                    trace_service_pb2_grpc.TraceServiceStub(channel).Export(
                        request, timeout=2
                    )
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                reader.join(timeout=2)
                self.assertFalse(reader.is_alive())
                log = ready_line + "".join(
                    lines.get_nowait() for _ in range(lines.qsize())
                )
                self.assertIn("received 1 span(s)", log)
                self.assertEqual(
                    json.loads(output.read_text(encoding="utf-8"))["resourceSpans"]
                    [0]["scopeSpans"][0]["spans"][0]["name"],
                    "llm_request",
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                reader.join(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()

    def test_timeout_without_spans_does_not_write_an_empty_document(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.json"
            result = run_receiver(port=0, output=output, timeout_seconds=0.05)

            self.assertEqual(result, 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
