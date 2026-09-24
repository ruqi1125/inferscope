import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliModuleTests(unittest.TestCase):
    def test_python_module_entrypoint_shows_help(self):
        environment = os.environ.copy()
        source_path = str(ROOT / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (source_path, environment.get("PYTHONPATH")))
        )

        result = subprocess.run(
            [sys.executable, "-m", "inferscope", "--help"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: inferscope", result.stdout)
        for command in ("summary", "inspect", "replay", "compare", "trace", "adapt"):
            self.assertIn(command, result.stdout)

    def test_vllm_example_flows_through_adapt_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            adapt = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "inferscope",
                    "adapt",
                    "vllm",
                    str(ROOT / "examples" / "demo-otel.json"),
                    "--output",
                    str(trace_path),
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(adapt.returncode, 0, adapt.stderr)

            summary = subprocess.run(
                [sys.executable, "-m", "inferscope", "summary", str(trace_path), "--json"],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(summary.returncode, 0, summary.stderr)
            request = json.loads(summary.stdout)["requests_detail"][0]

        self.assertEqual(request["input_tokens"], 256)
        self.assertEqual(request["queue_ns"], 5_000_000)
        self.assertEqual(request["prefill_ns"], 20_000_000)
        self.assertEqual(request["decode_ns"], 25_000_000)
        self.assertEqual(request["ttft_ns"], 25_000_000)
        self.assertEqual(request["e2e_ns"], 50_000_000)


if __name__ == "__main__":
    unittest.main()
