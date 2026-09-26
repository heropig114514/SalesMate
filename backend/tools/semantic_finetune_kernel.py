"""Responsibility: Run auditable QLoRA, weight merging, and four same-engine quantization comparisons in private Kaggle.
Implementation: Freezes synthetic data and the official revision; verifies memory-efficient attention before training LoRA and comparing F16 and Q4.
Relationships: build_semantic_corpus exports contract and data; this script neither connects to the business database nor automatically replaces deployment weights.
Directory:
- save: Save JSON.
- digest: Calculate artifact SHA256.
- command: Execute one command and retain its log.
- load_contract: Load frozen validation and entity-resolution functions.
- canonical_facts: Convert local entity keys into comparable semantic facts.
- score: Calculate raw and program-resolved observation metrics.
- evaluate: Execute one test set with uniform service parameters.
- benchmark: Measure CPU and GPU throughput on the same hardware.
- efficient_attention: Explicitly expand KV and force memory-efficient SDPA.
- verify_attention: Validate forward and gradient numerical consistency with small tensors.
- load_trainable: Initialize the QLoRA model under frozen conditions.
- target_loss: Calculate assistant-target cross entropy for complete input.
- memory_preflight: Validate forward/backward for the first complete training sample without updating weights.
- train: Run one fixed QLoRA training pass and save the adapter.
- remove_scratch: Safely delete temporary artifacts for this experiment.
- main: Entry point for preflight, baseline, training, merging, quantization, and comparison.
Variable index:
- CONFIG: Frozen conditions for the new experiment; does not override existing experiments or production configuration.
"""
import ast
import ctypes
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import zipfile

CONFIG = {"id": "salesmate-semantic-qlora-v2", "seed": 2026, "model": "Qwen/Qwen3-4B-Instruct-2507",
          "revision": "cdbee75f17c01a7cc42f958dc650907174af0554", "llama_commit": "7fe450e19305b828c199d602c23a8337aaa1f03b",
          "dataset": "salesmate-semantic-synthetic-v1", "train_rows": 96, "test_rows": 12, "epochs": 1,
          "max_sequence": 6144, "max_output": 1536, "lora_r": 8, "lora_alpha": 16, "lora_dropout": 0.05,
          "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"], "learning_rate": 0.0001,
          "micro_batch": 1, "gradient_accumulation": 4, "max_grad_norm": 1.0,
          "training_device": 0, "compute_dtype": "float16", "bnb_quant_type": "nf4", "bnb_double_quant": True,
          "eval_context": 16384, "eval_gpu_layers": 99, "eval_threads": 4, "bench_repetitions": 3,
          "attention_backend": "salesmate_efficient", "attention_probe_atol": 0.005, "attention_probe_rtol": 0.005,
          "packages": ["transformers==4.56.2", "peft==0.17.1", "accelerate==1.10.1", "bitsandbytes==0.47.0",
                       "huggingface-hub==0.34.4", "sentencepiece==0.2.1", "protobuf==4.25.8", "jsonschema==4.25.1"]}


# Function: Write an auditable JSON artifact.
# Inputs: `path` is the output file; `value` is serializable.
# Outputs: None; creates parent directories and saves a UTF-8 file.
# Logic: Rejects NaN to avoid masking training numeric errors.
# Constraints: Callers provide a unique run directory; per-sample files neither retry nor rewrite responses.
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


# Function: Calculate a file digest.
# Inputs: `path` is an existing path.
# Outputs: SHA256 string.
# Logic: Reads in chunks without changing the artifact.
# Constraints: I/O failures propagate.
def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# Function: Run one external build or conversion operation.
# Inputs: `args` is the argument array; `log` is the output path; `cwd` is an optional working directory.
# Outputs: None; failures raise CalledProcessError and retain the log.
# Logic: Does not use a shell; while waiting for the same process, records liveness every 30 seconds without retrying or changing arguments.
# Constraints: Callers select explicit paths and logs contain no credentials.
def command(args, log, cwd=None):
    logging.info("command_started executable=%s log=%s", args[0], log.name)
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(arg) for arg in args], cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        while True:
            try:
                status = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                logging.info("command_running log=%s pid=%s", log.name, process.pid)
        if status:
            raise subprocess.CalledProcessError(status, args)
    logging.info("command_completed log=%s", log.name)


# Function: Use frozen validation and entity-resolution functions identical to the system's.
# Inputs: `data` is the private synthetic-data directory with verified digest.
# Outputs: Namespace containing validate_extraction and resolve_entities.
# Logic: Removes only the relative schema import and injects the same static schema; does not rewrite function ASTs.
# Constraints: Executes only source uploaded and hash-verified by this experiment, never model output.
def load_contract(data):
    schema = json.loads((data / "schema.json").read_text())
    namespace = {"catalog": lambda: schema}
    tree = ast.parse((data / "semantic_contract.py").read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.module == "business_schema")]
    exec(compile(tree, "frozen_semantic_contract", "exec"), namespace)
    exec(compile((data / "entity_resolution.py").read_text(encoding="utf-8"), "frozen_entity_resolution", "exec"), namespace)
    return namespace


# Function: Compare relations and attributes independently of local-key order.
# Inputs: `extraction` is a valid extraction dictionary.
# Outputs: Set of subject type/name, predicate, object type/name, or attribute value.
# Logic: Does not require gold and model output to use identical e numbering or quotation lengths; source-text validity is checked separately.
# Constraints: Names and values must match exactly; fuzzy matching cannot raise scores.
def canonical_facts(extraction):
    entities = {item["key"]: (item["kind"], item["name"]) for item in extraction["entities"]}
    return {(entities[item["subject"]], item["predicate"], entities[item["object"]] if item["object"] else None,
             json.dumps(item["value"], ensure_ascii=False, sort_keys=True)) for item in extraction["facts"]}


# Function: Score one model output and its independent resolution step.
# Inputs: `result` is model JSON; `row` is a frozen test sample; `contract` is the production-validator namespace.
# Outputs: Structural/evidence validity, fact counts, raw and resolved exact identity matches, and errors.
# Logic: An invalid response fails the entire case and counts as missed detection; valid candidates are scored before and after resolution separately.
# Constraints: Does not repair JSON or retry, and does not attribute program-resolution effects to the model.
def score(result, row, contract):
    expected = canonical_facts(row["gold"])
    try:
        contract["validate_extraction"](result, row["text"], row["context"]["entities"])
        actual = canonical_facts(result)
        resolved, decisions = contract["resolve_entities"](result, row["context"])
        contract["validate_extraction"](resolved, row["text"], row["context"]["entities"])
        gold_ids = {(x["kind"], x["name"], x["existing_id"]) for x in row["gold"]["entities"]}
        raw_ids = {(x["kind"], x["name"], x["existing_id"]) for x in result["entities"]}
        resolved_ids = {(x["kind"], x["name"], x["existing_id"]) for x in resolved["entities"]}
        return {"valid": True, "tp": len(expected & actual), "fp": len(actual - expected), "fn": len(expected - actual),
                "facts_exact": actual == expected, "raw_identity_exact": raw_ids == gold_ids,
                "resolved_identity_exact": resolved_ids == gold_ids, "decisions": decisions}
    except (ValueError, KeyError, TypeError) as exc:
        return {"valid": False, "tp": 0, "fp": 0, "fn": len(expected), "facts_exact": False,
                "raw_identity_exact": False, "resolved_identity_exact": False, "error": str(exc)}


# Function: Measure one weight artifact with the same llama.cpp GPU service.
# Inputs: `model` is GGUF; `name` is the experiment arm; `binary` is the service executable; `rows` are the test set; `data` is the contract directory; `out` is the output directory.
# Outputs: Summarized quality and latency and saved raw response and score for every row.
# Logic: Fixes one GPU, disables automatic adaptation and caching, and generates once; service-readiness polling is not inference retry.
# Constraints: HTTP, budget, or model-identity errors terminate the experiment; format or semantic errors score as failed samples; no truncation.
def evaluate(model, name, binary, rows, data, out):
    import requests
    contract = load_contract(data)
    schema = json.loads((data / "response-schema.json").read_text())
    port = 8089
    args = [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port), "--alias", name,
            "-ngl", "99", "-sm", "none", "-mg", "0", "-fit", "off", "-t", "4", "-tb", "4", "-c", "16384", "-np", "1",
            "--cache-ram", "0", "--no-cache-prompt", "--no-context-shift", "--offline"]
    results = []
    with (out / f"{name}-server.log").open("w") as log, requests.Session() as session:
        session.trust_env = False
        process = subprocess.Popen([str(arg) for arg in args], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 180
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Model server exited before readiness")
                try:
                    if session.get(f"http://127.0.0.1:{port}/health", timeout=3).status_code == 200:
                        break
                except requests.ConnectionError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Model server readiness")
                time.sleep(1)
            for index, row in enumerate(rows):
                started = time.monotonic()
                payload = {"model": name, "messages": row["messages"], "temperature": 0, "seed": CONFIG["seed"], "max_tokens": CONFIG["max_output"], "stream": False,
                           "response_format": {"type": "json_schema", "json_schema": {"name": "crm_graph_observation", "strict": True, "schema": schema}}}
                response = session.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=payload, timeout=(10, 300), allow_redirects=False)
                response.raise_for_status()
                raw = response.json()
                elapsed = time.monotonic() - started
                save(out / name / f"{index:02d}-raw.json", {"id": row["id"], "seconds": elapsed, "response": raw})
                choice = raw["choices"][0]
                if raw["model"] != name or choice["finish_reason"] != "stop":
                    raise ValueError("model_identity_or_completion")
                try:
                    parsed = json.loads(choice["message"]["content"])
                except json.JSONDecodeError:
                    parsed = None
                metrics = score(parsed, row, contract)
                results.append({"id": row["id"], "family": row["family"], "seconds": elapsed, "usage": raw.get("usage"), **metrics})
                logging.info("evaluation arm=%s sample=%s valid=%s facts_exact=%s seconds=%.2f", name, row["id"], metrics["valid"], metrics["facts_exact"], elapsed)
        finally:
            process.terminate()
            process.wait(timeout=30)
    counts = {key: sum(row[key] for row in results) for key in ("tp", "fp", "fn", "valid", "facts_exact", "raw_identity_exact", "resolved_identity_exact")}
    denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
    summary = {"n": len(rows), **counts, "fact_micro_f1": 2 * counts["tp"] / denominator if denominator else 1.0,
               "mean_seconds": sum(row["seconds"] for row in results) / len(results), "results": results,
               "gguf_sha256": digest(model), "gguf_bytes": model.stat().st_size}
    save(out / f"{name}-summary.json", summary)
    return summary


# Function: Run fixed CPU/GPU microbenchmarks for the same artifact.
# Inputs: `binary` is llama-bench; `model` is GGUF; `name` is the experiment arm; `out` is the result directory.
# Outputs: One raw JSON and error log for CPU and GPU each, while recording each device test's start and completion.
# Logic: Repeats 128 input tokens and 32 generated tokens three times each with four threads; GPU mode uses GPU0 only.
# Constraints: This is Kaggle hardware throughput, not local latency; semantic-test scores do not estimate speed.
def benchmark(binary, model, name, out):
    for device, layers in (("cpu", "0"), ("gpu", "99")):
        logging.info("benchmark_started arm=%s device=%s", name, device)
        with (out / f"{name}-{device}-bench.json").open("w") as stream, (out / f"{name}-{device}-bench.log").open("w") as log:
            subprocess.run([str(binary), "-m", str(model), "-p", "128", "-n", "32", "-r", "3", "-t", "4", "-ngl", layers, "-sm", "none", "-mg", "0", "-o", "json"], stdout=stream, stderr=log, check=True)
        logging.info("benchmark_completed arm=%s device=%s", name, device)


# Function: Use the user-approved v2 approach to prevent native GQA from automatically selecting the math path.
# Inputs: `module` is the attention layer; `query`, `key`, and `value` are projection tensors; `attention_mask` is the mask; `dropout` is probability; `scaling` is scale; `is_causal` is the causal flag; `kwargs` are framework extras.
# Outputs: Attention result and None in Transformers layout.
# Logic: Explicitly repeats KV heads and forces EFFICIENT_ATTENTION; the first call on layer zero logs actual shapes, types, and mask state.
# Constraints: Supports only this experiment's cache-free, equal-length causal training; attention weights are unsupported; unavailable kernels fail directly and may not fall back to math.
def efficient_attention(module, query, key, value, attention_mask, dropout=0.0, scaling=None, is_causal=None, **kwargs):
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    if kwargs.get("output_attentions") or kwargs.get("head_mask") is not None:
        raise ValueError("attention_weights_not_supported")
    if query.shape[-2] != key.shape[-2] or key.shape != value.shape or query.shape[1] % key.shape[1]:
        raise ValueError("attention_training_shape")
    causal = (attention_mask is None and getattr(module, "is_causal", True)) if is_causal is None else is_causal
    if not causal and attention_mask is None:
        raise ValueError("causal_training_required")
    if getattr(module, "layer_idx", -1) == 0 and not getattr(module, "_salesmate_backend_logged", False):
        logging.info("attention_backend=EFFICIENT_ATTENTION query=%s key=%s dtype=%s mask=%s", tuple(query.shape), tuple(key.shape), query.dtype, attention_mask is not None)
        module._salesmate_backend_logged = True
    groups = query.shape[1] // key.shape[1]
    repeated_key = key.repeat_interleave(groups, dim=1)
    repeated_value = value.repeat_interleave(groups, dim=1)
    with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
        output = torch.nn.functional.scaled_dot_product_attention(query, repeated_key, repeated_value, attn_mask=attention_mask,
                                                                  dropout_p=dropout, is_causal=causal, scale=scaling, enable_gqa=False)
    return output.transpose(1, 2).contiguous(), None


# Function: Verify forward and backward numerical values for custom attention on real T4 hardware.
# Inputs: `out` is the audit directory; reads new-experiment preflight tolerances from CONFIG.
# Outputs: attention-preflight.json; stops when tolerance is not met or the kernel is unavailable.
# Logic: Compares 32 tokens with 32 Q heads and 8 KV heads against an FP32 math reference; records maximum absolute error and verifies Q/K/V gradients.
# Constraints: Tolerances apply only to implementation preflight and do not change quality scoring; random state is restored on context exit and test data is not used.
def verify_attention(out):
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from types import SimpleNamespace
    with torch.random.fork_rng(devices=[0]):
        torch.manual_seed(CONFIG["seed"])
        values = [torch.randn(1, heads, 32, 128, device="cuda:0", dtype=torch.float16) for heads in (32, 8, 8)]
        actual_inputs = [value.detach().clone().requires_grad_() for value in values]
        reference_inputs = [value.float().detach().requires_grad_() for value in values]
        actual = efficient_attention(SimpleNamespace(is_causal=True), *actual_inputs, attention_mask=None)[0].transpose(1, 2)
        with sdpa_kernel(SDPBackend.MATH):
            reference = torch.nn.functional.scaled_dot_product_attention(*reference_inputs, is_causal=True, enable_gqa=True)
        actual.float().sum().backward()
        reference.sum().backward()
        errors = {}
        for name, observed, expected in [("output", actual, reference)] + [(name, value.grad, ref.grad) for name, value, ref in zip(("query_grad", "key_grad", "value_grad"), actual_inputs, reference_inputs)]:
            torch.testing.assert_close(observed.float(), expected, atol=CONFIG["attention_probe_atol"], rtol=CONFIG["attention_probe_rtol"])
            errors[name] = float((observed.float() - expected).abs().max())
        save(out / "attention-preflight.json", {"passed": True, "backend": "EFFICIENT_ATTENTION", "reference": "FP32 math GQA", "max_absolute_errors": errors})
        logging.info("attention_preflight_passed errors=%s", errors)


# Function: Initialize identical seed, quantization, LoRA, and gradient-checkpoint configuration.
# Inputs: `snapshot` is official weights downloaded for this run; reads CONFIG and GPU0.
# Outputs: Tokenizer, train-state model, and list of trainable parameters.
# Logic: Registers attention and official SDPA masks through public interfaces; resets seeds on every initialization so diagnostics do not consume the formal training random sequence.
# Constraints: Only the attention computation path is a user-approved v2 change; other training conditions match v1 and never degrade automatically.
def load_trainable(snapshot):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig, AttentionInterface, AttentionMaskInterface
    from transformers.masking_utils import sdpa_mask
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    torch.manual_seed(CONFIG["seed"])
    random.seed(CONFIG["seed"])
    AttentionInterface.register(CONFIG["attention_backend"], efficient_attention)
    AttentionMaskInterface.register(CONFIG["attention_backend"], sdpa_mask)
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16)
    model = AutoModelForCausalLM.from_pretrained(snapshot, local_files_only=True, torch_dtype=torch.float16, device_map={"": 0}, quantization_config=quant, attn_implementation=CONFIG["attention_backend"])
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, target_modules=CONFIG["target_modules"], lora_dropout=0.05, bias="none", task_type="CAUSAL_LM"))
    model.config.use_cache = False
    model.train()
    return tokenizer, model, [parameter for parameter in model.parameters() if parameter.requires_grad]


# Function: Calculate untruncated assistant-target cross entropy.
# Inputs: `model` is the training model; `tokenizer` is the official tokenizer; `row` is one training row.
# Outputs: Loss with computation graph and complete token count.
# Logic: The complete prompt participates in attention and loss uses only target logits, retaining v1 target weighting.
# Constraints: Prefix mismatch, exceeding 6144, or non-finite loss fails; schema and samples are not clipped.
def target_loss(model, tokenizer, row):
    import torch
    prefix = tokenizer.apply_chat_template(row["messages"], tokenize=True, add_generation_prompt=True)
    tokens = tokenizer.apply_chat_template(row["messages"] + [{"role": "assistant", "content": json.dumps(row["gold"], ensure_ascii=False, separators=(",", ":"))}], tokenize=True)
    if tokens[:len(prefix)] != prefix or len(tokens) > CONFIG["max_sequence"]:
        raise ValueError("training_token_contract")
    target_length = len(tokens) - len(prefix)
    inputs = torch.tensor([tokens], dtype=torch.long, device="cuda:0")
    with torch.autocast("cuda", dtype=torch.float16):
        logits = model(input_ids=inputs, attention_mask=torch.ones_like(inputs), use_cache=False, logits_to_keep=target_length + 1).logits[:, :-1]
        loss = torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), inputs[:, -target_length:].reshape(-1))
    if not torch.isfinite(loss):
        raise FloatingPointError("nonfinite_training_loss")
    return loss, len(tokens)


# Function: Verify that the first complete sample can complete training forward and backward before building inference tools.
# Inputs: `snapshot` is official weights; `rows` is the frozen training set; `out` is the audit directory.
# Outputs: memory-preflight.json; creates neither optimizer updates nor adapter weights.
# Logic: Uses the same initialization, sample order, and scaled loss as formal training, records load/forward/backward memory, then releases the model.
# Constraints: Does not shorten samples or change parameters, excludes diagnostics from 24 steps, and formal training reinitializes from the same seed.
def memory_preflight(snapshot, rows, out):
    import torch
    logging.info("memory_preflight_started no_optimizer_updates")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(0)
    tokenizer, model, parameters = load_trainable(snapshot)
    order = list(range(len(rows)))
    random.Random(CONFIG["seed"]).shuffle(order)
    row = rows[order[0]]
    audit = {"sample": row["id"], "model_loaded_bytes": torch.cuda.memory_allocated(0), "optimizer_updates": 0}
    save(out / "memory-preflight-start.json", audit)
    loss, length = target_loss(model, tokenizer, row)
    audit.update({"tokens": length, "loss": float(loss.detach()), "forward_bytes": torch.cuda.memory_allocated(0)})
    scaler = torch.amp.GradScaler("cuda", init_scale=256)
    scaler.scale(loss / CONFIG["gradient_accumulation"]).backward()
    gradients = [parameter.grad for parameter in parameters if parameter.grad is not None]
    if not gradients or not all(torch.isfinite(gradient).all() for gradient in gradients) or not any(torch.any(gradient != 0) for gradient in gradients):
        raise FloatingPointError("preflight_gradients_invalid")
    audit.update({"passed": True, "peak_allocated_bytes": torch.cuda.max_memory_allocated(0), "peak_reserved_bytes": torch.cuda.max_memory_reserved(0)})
    save(out / "memory-preflight.json", audit)
    logging.info("memory_preflight_passed %s", audit)
    del gradients, loss, scaler, parameters, model, tokenizer
    gc.collect()
    torch.cuda.empty_cache()


# Function: Run one fixed single-T4 QLoRA training pass.
# Inputs: `snapshot` is the official Hugging Face weights; `rows` contains training data only; `out` is the output root.
# Outputs: A LoRA adapter, training-step logs, and numerical/parameter audits.
# Logic: Initialize NF4/r8 LoRA with v2 attention; use complete prompts in the target loss, accumulate four samples per update, and run 24 steps without counting diagnostic steps.
# Constraints: Do not truncate samples or select a model with test data; fail immediately for non-finite loss or gradients, with no automatic retry or fallback.
def train(snapshot, rows, out):
    import torch
    tokenizer, model, parameters = load_trainable(snapshot)
    torch.cuda.reset_peak_memory_stats(0)
    optimizer = torch.optim.AdamW(parameters, lr=CONFIG["learning_rate"], weight_decay=0.0)
    scaler = torch.amp.GradScaler("cuda", init_scale=256)
    order = list(range(len(rows)))
    random.Random(CONFIG["seed"]).shuffle(order)
    optimizer.zero_grad(set_to_none=True)
    logs, running = [], 0.0
    started = time.monotonic()
    for position, row_index in enumerate(order):
        row = rows[row_index]
        loss, _ = target_loss(model, tokenizer, row)
        running += float(loss.detach())
        scaler.scale(loss / CONFIG["gradient_accumulation"]).backward()
        del loss
        if (position + 1) % CONFIG["gradient_accumulation"] == 0:
            scaler.unscale_(optimizer)
            norm = torch.nn.utils.clip_grad_norm_(parameters, CONFIG["max_grad_norm"], error_if_nonfinite=True)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            entry = {"step": len(logs) + 1, "samples_seen": position + 1, "loss": running / CONFIG["gradient_accumulation"],
                     "grad_norm": float(norm), "elapsed_seconds": time.monotonic() - started}
            logs.append(entry)
            running = 0.0
            save(out / "training-progress.json", logs)
            logging.info("train_step %s", json.dumps(entry))
    if len(logs) != 24:
        raise ValueError("training_step_count")
    model.save_pretrained(out / "adapter", safe_serialization=True)
    tokenizer.save_pretrained(out / "adapter")
    save(out / "training-audit.json", {"steps": len(logs), "rows": len(rows), "trainable_parameters": sum(p.numel() for p in parameters),
                                       "seconds": time.monotonic() - started, "peak_cuda_bytes": torch.cuda.max_memory_allocated(0),
                                       "order": [rows[index]["id"] for index in order], "adapter_sha256": digest(out / "adapter/adapter_model.safetensors")})
    del model, parameters, optimizer, scaler
    gc.collect()
    torch.cuda.empty_cache()


# Function: Remove a completed stage's dedicated temporary artifact to control disk use.
# Inputs: `path` is the target to clean; `scratch` is the temporary root created for this experiment.
# Outputs: None; deletes the target without deleting outputs or another user's cache.
# Logic: Confirm that the resolved absolute target lies strictly below `scratch` and is not `scratch` itself.
# Constraints: Use only for files generated or downloaded by this run; do not cross shells or process user business files.
def remove_scratch(path, scratch):
    path, root = path.resolve(), scratch.resolve()
    if path == root or root not in path.parents:
        raise ValueError("unsafe_scratch_path")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


# Function: Run independent fine-tuning and quantization comparisons.
# Inputs: No function arguments; requires the private Kaggle data mount and T4 resources.
# Outputs: An adapter, fine-tuned Q4 GGUF, four groups of raw predictions, metrics, and microbenchmarks, plus environment and artifact summaries; retains diagnostics and exits on failure.
# Logic: Perform numerical and full-sample VRAM preflight checks before building, baselining, and formal training; explicitly constrain PyTorch and keep temporary artifacts only under the isolated `/tmp` directory.
# Constraints: Do not install an alternative model, resume a failed step, or change test criteria; stop immediately when data or hardware does not match requirements.
def main():
    out, scratch = Path("/kaggle/working/semantic-ft-v2"), Path("/tmp/salesmate-semantic-ft-v2-scratch")
    out.mkdir(exist_ok=False)
    scratch.mkdir(exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), logging.FileHandler(out / "run.log")])
    stage = "environment"
    try:
        os.environ["USE_TF"] = "0"
        os.environ["USE_FLAX"] = "0"
        os.environ["USE_TORCH"] = "1"
        logging.info("backend_selected pytorch_only USE_TF=0 USE_FLAX=0 USE_TORCH=1")
        save(out / "config.json", CONFIG)
        command([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *CONFIG["packages"]], out / "install.log")
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
        import requests
        if torch.cuda.device_count() != 2 or any("T4" not in torch.cuda.get_device_name(i) for i in range(2)):
            raise RuntimeError("This experiment requires the preflight dual T4 environment")
        save(out / "environment.json", {"gpus": [torch.cuda.get_device_name(i) for i in range(2)], "torch": torch.__version__, "python": sys.version,
                                        "packages": {item.split("==")[0]: importlib.metadata.version(item.split("==")[0]) for item in CONFIG["packages"]},
                                        "backend": "pytorch_only", "disk_free": shutil.disk_usage(scratch).free, "cpu": subprocess.check_output(["lscpu"], text=True)})
        matches = list(Path("/kaggle/input").rglob("manifest.json"))
        if len(matches) != 1:
            raise ValueError("Expected exactly one synthetic corpus")
        data = matches[0].parent
        manifest = json.loads(matches[0].read_text())
        for name, expected in manifest["sha256"].items():
            if digest(data / name) != expected:
                raise ValueError("Dataset hash mismatch: " + name)
        save(out / "dataset-manifest.json", manifest)
        train_rows = [json.loads(line) for line in (data / "train.jsonl").read_text().splitlines()]
        test_rows = [json.loads(line) for line in (data / "test.jsonl").read_text().splitlines()]
        if len(train_rows) != 96 or len(test_rows) != 12:
            raise ValueError("Frozen sample count mismatch")
        stage = "download_official_model"
        snapshot = Path(snapshot_download(CONFIG["model"], revision=CONFIG["revision"], local_dir=scratch / "base-hf", allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.jinja"]))
        save(out / "base-model-hashes.json", {file.name: digest(file) for file in snapshot.iterdir() if file.is_file()})
        tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        lengths = []
        for split in ("train", "dev", "test"):
            for line in (data / f"{split}.jsonl").read_text().splitlines():
                row = json.loads(line)
                prefix = tokenizer.apply_chat_template(row["messages"], tokenize=True, add_generation_prompt=True)
                complete = tokenizer.apply_chat_template(row["messages"] + [{"role": "assistant", "content": json.dumps(row["gold"], ensure_ascii=False, separators=(",", ":"))}], tokenize=True)
                if complete[:len(prefix)] != prefix or len(complete) > CONFIG["max_sequence"]:
                    raise ValueError("Preflight token prefix/length")
                lengths.append({"id": row["id"], "prompt": len(prefix), "total": len(complete)})
        save(out / "token-preflight.json", lengths)
        stage = "attention_preflight"
        verify_attention(out)
        stage = "memory_preflight"
        memory_preflight(snapshot, train_rows, out)
        stage = "build_llama"
        archive = scratch / "llama.zip"
        response = requests.get("https://api.github.com/repos/ggml-org/llama.cpp/zipball/" + CONFIG["llama_commit"], timeout=120)
        response.raise_for_status()
        archive.write_bytes(response.content)
        save(out / "llama-source.json", {"commit": CONFIG["llama_commit"], "archive_sha256": digest(archive)})
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if scratch.resolve() not in (scratch / member).resolve().parents:
                    raise ValueError("archive_path")
            bundle.extractall(scratch)
        source = next(scratch.glob("ggml-org-llama.cpp-*"))
        build = source / "build"
        ctypes.CDLL("libcuda.so.1")
        driver_paths = {line.split(maxsplit=5)[-1].strip() for line in Path("/proc/self/maps").read_text().splitlines()
                        if len(line.split(maxsplit=5)) == 6 and "libcuda.so" in Path(line.split(maxsplit=5)[-1].strip()).name}
        if len(driver_paths) != 1:
            raise RuntimeError(f"Expected one loaded CUDA driver library; found {len(driver_paths)} paths: {sorted(driver_paths)}")
        driver_path = Path(next(iter(driver_paths))).resolve(strict=True)
        save(out / "cuda-driver-discovery.json", {"method": "process_mapped_driver", "driver_library": str(driver_path), "bytes": driver_path.stat().st_size})
        command(["cmake", "-S", source, "-B", build, "-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=75", f"-DCUDA_cuda_driver_LIBRARY={driver_path}", "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF", "-DCMAKE_BUILD_TYPE=Release"], out / "cmake-configure.log")
        command(["cmake", "--build", build, "--config", "Release", "-j", "2", "--target", "llama-server", "llama-quantize", "llama-bench"], out / "cmake-build.log")
        binary = build / "bin"
        base_f16, base_q4 = scratch / "base-F16.gguf", scratch / "base-Q4_K_M.gguf"
        stage = "baseline_conversion"
        command([sys.executable, source / "convert_hf_to_gguf.py", snapshot, "--outfile", base_f16, "--outtype", "f16"], out / "base-convert.log")
        command([binary / "llama-quantize", base_f16, base_q4, "Q4_K_M", "4"], out / "base-quantize.log")
        summaries = {}
        for name, model in (("base-f16", base_f16), ("base-q4", base_q4)):
            stage = name
            summaries[name] = evaluate(model, name, binary / "llama-server", test_rows, data, out)
            benchmark(binary / "llama-bench", model, name, out)
        remove_scratch(base_f16, scratch)
        remove_scratch(base_q4, scratch)
        stage = "qlora_training"
        train(snapshot, train_rows, out)
        stage = "merge_fp16"
        base = AutoModelForCausalLM.from_pretrained(snapshot, local_files_only=True, torch_dtype=torch.float16, device_map={"": "cpu"}, low_cpu_mem_usage=True)
        merged = PeftModel.from_pretrained(base, out / "adapter").merge_and_unload(safe_merge=True)
        # Official weights and tokenizer are loaded; delete only this run's dedicated copy to free disk space for merged artifacts.
        remove_scratch(snapshot, scratch)
        merged_dir = scratch / "merged-hf"
        merged.save_pretrained(merged_dir, safe_serialization=True, max_shard_size="2GB")
        tokenizer.save_pretrained(merged_dir)
        del base, merged
        gc.collect()
        torch.cuda.empty_cache()
        tuned_f16, tuned_q4 = scratch / "tuned-F16.gguf", out / "Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf"
        stage = "tuned_conversion"
        command([sys.executable, source / "convert_hf_to_gguf.py", merged_dir, "--outfile", tuned_f16, "--outtype", "f16"], out / "tuned-convert.log")
        remove_scratch(merged_dir, scratch)
        command([binary / "llama-quantize", tuned_f16, tuned_q4, "Q4_K_M", "4"], out / "tuned-quantize.log")
        for name, model in (("tuned-f16", tuned_f16), ("tuned-q4", tuned_q4)):
            stage = name
            summaries[name] = evaluate(model, name, binary / "llama-server", test_rows, data, out)
            benchmark(binary / "llama-bench", model, name, out)
        save(out / "comparison.json", {"completed": True, "summaries": summaries, "limitations": ["12 synthetic held-out scenarios", "Not a production accuracy estimate", "GPU quality evaluation; CPU and GPU speed measured separately", "No automatic model deployment"]})
        remove_scratch(tuned_f16, scratch)
        logging.info("EXPERIMENT_COMPLETE output=%s", out)
    except Exception as exc:
        save(out / "failure.json", {"stage": stage, "type": type(exc).__name__, "message": str(exc)})
        logging.exception("EXPERIMENT_FAILED stage=%s no_automatic_retry", stage)
        raise


if __name__ == "__main__":
    main()
