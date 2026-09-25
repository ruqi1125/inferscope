"""用已有模型验证原生统计回调；仅执行两条固定合成请求。"""

import argparse
import asyncio
import json
from pathlib import Path

from inferscope.runtime.vllm_stats import make_stat_logger, read_stats, summarize_stats


async def run(model, output_directory):
    from vllm import SamplingParams
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.v1.engine.async_llm import AsyncLLM

    logger = make_stat_logger(output_directory)
    engine = AsyncLLM.from_engine_args(
        AsyncEngineArgs(model=model, max_model_len=2048, max_num_seqs=1,
                        gpu_memory_utilization=0.85, enforce_eager=True,
                        enable_prefix_caching=True, disable_log_stats=False),
        stat_loggers=[logger],
    )
    prompt = "Summarize this passage: " + "A river crosses a quiet valley with trees and farms. " * 32
    expected = {}
    try:
        for request_id in ("req-m3-cold", "req-m3-warm"):
            final = None
            async for result in engine.generate(
                prompt, SamplingParams(temperature=0, max_tokens=8), request_id=request_id,
            ):
                final = result
            assert final is not None and final.finished
            expected[request_id] = {
                "input_tokens": len(final.prompt_token_ids),
                "output_tokens": len(final.outputs[0].token_ids),
                "cached_tokens": final.num_cached_tokens,
            }
        await asyncio.sleep(0.2)
    finally:
        engine.shutdown()

    files = list(Path(output_directory).glob("vllm-stats-*.jsonl"))
    assert len(files) == 1, "测试只应产生一个 engine 统计文件"
    report = summarize_stats(read_stats(files[0]))
    assert len(report["requests"]) == 2
    for request in report["requests"]:
        assert not request["ambiguous"]
        for field, value in expected[request["request_id"]].items():
            assert request[field] == value, (request["request_id"], field)
    assert expected["req-m3-cold"]["cached_tokens"] == 0
    assert expected["req-m3-warm"]["cached_tokens"] > 0
    print(json.dumps({"verified_requests": expected,
                      "scheduler_samples": len(report["engines"][0]["scheduler_samples"])}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.model, args.output_directory))
