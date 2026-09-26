"""Responsibility: Independently recompute Kaggle fine-tuning and quantization output and verify downloaded weights.
Implementation: Checks v2 attention/memory preflight, data hashes, training steps, nonzero LoRA, and four groups of raw responses and microbenchmarks.
Relationships: semantic_finetune_kernel provides the same frozen scoring implementation; official and fine-tuned artifacts are not automatically deployed because of scores.
Directory:
- read_json: Read JSON according to the file BOM.
- bench_rows: Extract and validate three 128/32 microbenchmark runs.
- main: Verify four experiment arms and local benchmarks and write the report.
Variable index:
- ARMS: The four experiment arms that must all be present.
"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
from semantic_finetune_kernel import CONFIG, digest, load_contract, score, save

ARMS = ("base-f16", "base-q4", "tuned-f16", "tuned-q4")


# Function: Read JSON from Linux output and PowerShell redirection.
# Inputs: `path` is a JSON file.
# Outputs: Parsed object.
# Logic: Uses UTF-16 when a UTF-16 BOM is present and UTF-8-SIG explicitly for other files.
# Constraints: Does not attempt to repair invalid JSON or alter numeric values.
def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"))


# Function: Extract throughput data with a fixed repetition count.
# Inputs: `path` is llama-bench JSON.
# Outputs: Prefill/decode throughput and hardware identifier.
# Logic: Requires exactly two benchmarks, 128 input and 32 generated tokens, each with three samples.
# Constraints: Summarizes speed only and does not infer real email end-to-end latency.
def bench_rows(path):
    rows = read_json(path)
    if len(rows) != 2 or {(x["n_prompt"], x["n_gen"]) for x in rows} != {(128, 0), (0, 32)}:
        raise ValueError("benchmark_shape")
    if any(len(x["samples_ts"]) != 3 or x["avg_ts"] <= 0 for x in rows):
        raise ValueError("benchmark_samples")
    return {"prefill": next(x["avg_ts"] for x in rows if x["n_prompt"]), "decode": next(x["avg_ts"] for x in rows if x["n_gen"]),
            "cpu": rows[0]["cpu_info"], "gpu": rows[0]["gpu_info"], "threads": rows[0]["n_threads"], "gpu_layers": rows[0]["n_gpu_layers"]}


# Function: Verify the complete experiment and generate an independent report.
# Inputs: No function parameters; CLI results is the downloaded directory, corpus is frozen data, local-bench-root is the local baseline, and output is a new directory.
# Outputs: verification.json and REPORT.md; any audit contradiction fails explicitly.
# Logic: Checks v2 preflight, seed order, step counts, and finite values, then recomputes every question; keeps identity metrics separate and records generated length to explain latency.
# Constraints: Requires safetensors/numpy to read adapters, does not call models or databases, and does not remove questions, rerun, or select models based on results.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--local-bench-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if read_json(args.results / "config.json") != CONFIG:
        raise ValueError("experiment_config_changed")
    manifest = read_json(args.corpus / "manifest.json")
    if read_json(args.results / "dataset-manifest.json") != manifest:
        raise ValueError("dataset_manifest_changed")
    for name, expected in manifest["sha256"].items():
        if digest(args.corpus / name) != expected:
            raise ValueError("corpus_changed")
    attention = read_json(args.results / "attention-preflight.json")
    memory = read_json(args.results / "memory-preflight.json")
    if not attention["passed"] or attention["backend"] != "EFFICIENT_ATTENTION" or not memory["passed"] or memory["optimizer_updates"] != 0:
        raise ValueError("attention_or_memory_preflight")
    training = read_json(args.results / "training-audit.json")
    progress = read_json(args.results / "training-progress.json")
    if training["steps"] != 24 or training["rows"] != 96 or len(progress) != 24 or progress[-1]["samples_seen"] != 96:
        raise ValueError("training_incomplete")
    train_ids = [json.loads(line)["id"] for line in (args.corpus / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    expected_order = list(train_ids)
    random.Random(CONFIG["seed"]).shuffle(expected_order)
    if training["order"] != expected_order or len(set(training["order"])) != 96:
        raise ValueError("training_order_or_split")
    if any(entry["step"] != index or entry["samples_seen"] != 4 * index or not all(math.isfinite(entry[key]) for key in ("loss", "grad_norm", "elapsed_seconds")) for index, entry in enumerate(progress, 1)):
        raise ValueError("training_progress_contract")
    if memory["sample"] != training["order"][0] or memory["tokens"] != 4324:
        raise ValueError("memory_preflight_sample_changed")
    adapter = args.results / "adapter/adapter_model.safetensors"
    if digest(adapter) != training["adapter_sha256"]:
        raise ValueError("adapter_hash")
    from safetensors.numpy import load_file
    import numpy as np
    tensors = load_file(adapter)
    updates = [value for name, value in tensors.items() if "lora_B" in name]
    if not updates or not all(np.isfinite(value).all() and np.any(value != 0) for value in updates):
        raise ValueError("adapter_updates_invalid")
    comparison = read_json(args.results / "comparison.json")
    if not comparison["completed"] or set(comparison["summaries"]) != set(ARMS):
        raise ValueError("four_arm_comparison_incomplete")
    rows = [json.loads(line) for line in (args.corpus / "test.jsonl").read_text(encoding="utf-8").splitlines()]
    contract = load_contract(args.corpus)
    summaries, speed = {}, {}
    for arm in ARMS:
        stored = read_json(args.results / f"{arm}-summary.json")
        if stored != comparison["summaries"][arm] or len(stored["results"]) != len(rows):
            raise ValueError("summary_mismatch")
        for index, row in enumerate(rows):
            raw = read_json(args.results / arm / f"{index:02d}-raw.json")
            choice = raw["response"]["choices"][0]
            if raw["id"] != row["id"] or raw["response"]["model"] != arm or choice["finish_reason"] != "stop":
                raise ValueError("raw_response_identity")
            entry = stored["results"][index]
            if entry["id"] != row["id"] or entry["family"] != row["family"] or entry["usage"] != raw["response"].get("usage"):
                raise ValueError("raw_response_metadata")
            if not math.isfinite(raw["seconds"]) or raw["seconds"] <= 0 or entry["seconds"] != raw["seconds"]:
                raise ValueError("raw_response_latency")
            try:
                result = json.loads(choice["message"]["content"])
            except json.JSONDecodeError:
                result = None
            recomputed = score(result, row, contract)
            if any(stored["results"][index][key] != value for key, value in recomputed.items()):
                raise ValueError("score_recalculation_mismatch")
        totals = {key: sum(x[key] for x in stored["results"]) for key in ("tp", "fp", "fn", "valid", "facts_exact", "raw_identity_exact", "resolved_identity_exact")}
        if any(stored[key] != value for key, value in totals.items()):
            raise ValueError("aggregate_mismatch")
        denominator = 2 * totals["tp"] + totals["fp"] + totals["fn"]
        expected_f1 = 2 * totals["tp"] / denominator if denominator else 1.0
        if not math.isclose(stored["fact_micro_f1"], expected_f1) or not math.isclose(stored["mean_seconds"], statistics.mean(x["seconds"] for x in stored["results"])):
            raise ValueError("aggregate_f1_or_latency")
        summaries[arm] = {key: value for key, value in stored.items() if key != "results"}
        summaries[arm]["mean_completion_tokens"] = statistics.mean(entry["usage"]["completion_tokens"] for entry in stored["results"])
        summaries[arm]["mean_prompt_tokens"] = statistics.mean(entry["usage"]["prompt_tokens"] for entry in stored["results"])
        speed[arm] = {device: bench_rows(args.results / f"{arm}-{device}-bench.json") for device in ("cpu", "gpu")}
    for device in ("cpu", "gpu"):
        hardware = {tuple(speed[arm][device][key] for key in ("cpu", "gpu", "threads", "gpu_layers")) for arm in ARMS}
        if len(hardware) != 1:
            raise ValueError("benchmark_hardware_mismatch")
    quantized = args.results / "Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf"
    if digest(quantized) != summaries["tuned-q4"]["gguf_sha256"] or summaries["tuned-q4"]["gguf_sha256"] == summaries["base-q4"]["gguf_sha256"]:
        raise ValueError("quantized_artifact_identity")
    local = {name: bench_rows(args.local_bench_root / f"local-base-{name}-bench.json") for name in ("f16", "q4")}
    if any(local["f16"][key] != local["q4"][key] for key in ("cpu", "gpu", "threads", "gpu_layers")):
        raise ValueError("local_benchmark_hardware_mismatch")
    verification = {"verified": True, "summaries": summaries, "training": training, "attention_preflight": attention, "memory_preflight": memory, "kaggle_speed": speed, "local_speed": local,
                    "local_q4_speedup": {phase: local["q4"][phase] / local["f16"][phase] for phase in ("prefill", "decode")},
                    "loss_first_four": statistics.mean(x["loss"] for x in progress[:4]), "loss_last_four": statistics.mean(x["loss"] for x in progress[-4:]),
                    "limitation": "Synthetic shared-template pilot; no broad accuracy or deployment claim"}
    save(args.output / "verification.json", verification)
    lines = ["# 微调与量化独立核验", "", "四组原始预测与评分已复算；LoRA确有非零更新，下载的Q4制品哈希吻合。", "",
             "| 模型 | 证据合法 | 事实精确 | 模型身份精确 | 解析后身份精确 | 事实micro F1 | 平均GPU请求秒 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, value in summaries.items():
        lines.append(f"| {arm} | {value['valid']}/12 | {value['facts_exact']}/12 | {value['raw_identity_exact']}/12 | {value['resolved_identity_exact']}/12 | {value['fact_micro_f1']:.3f} | {value['mean_seconds']:.2f} |")
    lines += ["", "样本只有12个合成场景，测试身份与训练分离但模板共享。精确匹配依赖固定标注政策，需结合原文检查其他合理关系。训练损失下降不等于真实邮件准确率提升。",
              "", "请求延迟同时受生成长度影响；各组平均prompt/completion tokens及固定长度吞吐见 verification.json。程序实体解析不能冒充模型独立能力。未自动替换部署权重。"]
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "summaries": summaries, "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
