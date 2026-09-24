#!/usr/bin/env python3
"""Temporary loopback-only OTLP trace receiver for the vLLM live smoke test."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
import math
from pathlib import Path
import signal
import threading

import grpc
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)


LOGGER = logging.getLogger("vllm_otel_receiver")


class TraceReceiver(trace_service_pb2_grpc.TraceServiceServicer):
    """Accumulate OTLP resource span batches without logging their contents."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resource_spans: list[dict[str, object]] = []

    def Export(self, request, context):  # noqa: N802 - generated gRPC API
        try:
            document = MessageToDict(request, preserving_proto_field_name=False)
        except Exception:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid OTLP trace payload")

        with self._lock:
            self._resource_spans.extend(document.get("resourceSpans", []))
        return trace_service_pb2.ExportTraceServiceResponse()

    def document(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {"resourceSpans": deepcopy(self._resource_spans)}


def _span_count(document: dict[str, list[dict[str, object]]]) -> int:
    return sum(
        len(scope.get("spans", []))
        for resource in document["resourceSpans"]
        for scope in resource.get("scopeSpans", [])
    )


def run_receiver(port: int, output: Path, timeout_seconds: float) -> int:
    if (
        not 0 <= port <= 65535
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        return 2

    receiver = TraceReceiver()
    executor = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(executor)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
    bound_port = server.add_insecure_port(f"127.0.0.1:{port}")
    if bound_port == 0:
        executor.shutdown(wait=True, cancel_futures=True)
        return 2

    stop_requested = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_requested.set()

    handled_signals = (signal.SIGINT, signal.SIGTERM)
    old_handlers = {sig: signal.signal(sig, request_stop) for sig in handled_signals}
    try:
        server.start()
        LOGGER.info("listening on 127.0.0.1:%d", bound_port)
        stop_requested.wait(timeout_seconds)
    finally:
        server.stop(grace=0).wait()
        executor.shutdown(wait=True, cancel_futures=True)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)

    document = receiver.document()
    count = _span_count(document)
    LOGGER.info("received %d span(s)", count)
    if count == 0:
        return 1

    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
    except OSError:
        return 2
    return 0


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timeout must be a number of seconds"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return timeout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=_port, default=4317)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=_timeout, default=300)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run_receiver(args.port, args.output, args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
