"""职责：在私有 Kaggle 运行可审计的 QLoRA、权重合并和四组同引擎量化对照。
实现：固定合成数据与官方revision；先核验显存优化注意力，再训练LoRA并进行F16/Q4对照。
关联：build_semantic_corpus 导出协议和数据；本脚本不连接业务数据库或自动替换部署权重。
目录：
- save：保存JSON。
- digest：计算制品SHA256。
- command：执行单次命令并保留日志。
- load_contract：载入冻结的校验和解析函数。
- canonical_facts：将局部实体键转成可比较语义事实。
- score：计算原始及程序解析后的观察指标。
- evaluate：以统一服务参数执行一次测试集。
- benchmark：测量同硬件CPU和GPU吞吐。
- efficient_attention：显式展开KV并强制节省显存的SDPA。
- verify_attention：以小张量校验前向及梯度的数值一致性。
- load_trainable：按冻结条件初始化QLoRA模型。
- target_loss：计算完整输入的assistant目标损失。
- memory_preflight：验证首条完整训练样本前后向，不更新权重。
- train：执行固定一轮QLoRA并保存适配器。
- remove_scratch：安全删除本实验临时制品。
- main：预检、基线、训练、合并、量化与对照入口。
变量索引：
- CONFIG：新实验冻结条件，不覆盖既有实验或生产配置。
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


# 功能：写可复核JSON制品。
# 输入：`path` 输出文件；`value` 可序列化值。
# 输出：无；父目录创建并保存UTF-8文件。
# 逻辑：拒绝NaN以避免掩盖训练数值错误。
# 约束：上层负责唯一运行目录，逐样本文件不重试或改写响应。
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


# 功能：计算文件摘要。
# 输入：`path` 已存在路径。
# 输出：SHA256字符串。
# 逻辑：分块读取，不改变制品。
# 约束：IO失败传播。
def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# 功能：运行一次外部构建或转换操作。
# 输入：`args` 为参数数组；`log` 为输出路径；`cwd` 可选工作目录。
# 输出：无；失败抛CalledProcessError并保留日志。
# 逻辑：不使用shell；等待同一进程时每30秒记录存活状态，不重试或改变参数。
# 约束：上层选取明确路径，日志不含凭据。
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


# 功能：使用与系统相同的冻结校验和实体解析函数。
# 输入：`data` 为已核验摘要的私有合成数据目录。
# 输出：包含validate_extraction和resolve_entities的命名空间。
# 逻辑：仅移除相对schema导入，注入同一静态schema；函数AST不改写。
# 约束：仅执行本实验自行上传且已核验哈希的源码，不执行模型输出。
def load_contract(data):
    schema = json.loads((data / "schema.json").read_text())
    namespace = {"catalog": lambda: schema}
    tree = ast.parse((data / "semantic_contract.py").read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.module == "business_schema")]
    exec(compile(tree, "frozen_semantic_contract", "exec"), namespace)
    exec(compile((data / "entity_resolution.py").read_text(encoding="utf-8"), "frozen_entity_resolution", "exec"), namespace)
    return namespace


# 功能：比较与局部键顺序无关的关系及属性。
# 输入：`extraction` 合法抽取字典。
# 输出：主语类型/名称、谓词、宾语类型/名称或属性值的集合。
# 逻辑：不要求gold和模型使用相同e编号或相同长度引句，原文合法性另行校验。
# 约束：名称和值精确匹配，不用模糊匹配提高分数。
def canonical_facts(extraction):
    entities = {item["key"]: (item["kind"], item["name"]) for item in extraction["entities"]}
    return {(entities[item["subject"]], item["predicate"], entities[item["object"]] if item["object"] else None,
             json.dumps(item["value"], ensure_ascii=False, sort_keys=True)) for item in extraction["facts"]}


# 功能：评分单次模型输出及独立解析步骤。
# 输入：`result` 模型JSON；`row` 冻结测试样本；`contract` 正式校验函数命名空间。
# 输出：结构/证据有效性、事实计数、原始及解析后的身份精确匹配和错误。
# 逻辑：无效响应计整题失败且计入漏检；有效候选在解析前后分别评分。
# 约束：不修复JSON、不重试、不把程序解析效果归因于模型。
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


# 功能：以同一llama.cpp GPU服务测量一个权重制品。
# 输入：`model` GGUF；`name` 实验臂；`binary` 服务程序；`rows` 测试集；`data` 协议目录；`out` 输出目录。
# 输出：汇总质量/延迟，逐条保存原始响应和评分。
# 逻辑：固定单GPU、关闭自动适配及缓存、一次生成；服务就绪轮询不是推理重试。
# 约束：HTTP/预算/模型标识错误终止实验；格式或语义错误作为失败样本计分；不截断。
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


# 功能：运行同一制品的固定CPU/GPU微基准。
# 输入：`binary` llama-bench；`model` GGUF；`name` 实验臂；`out` 结果目录。
# 输出：CPU/GPU各一份原始JSON和错误日志。
# 逻辑：128输入tokens与32生成tokens各重复3次，保持4线程；GPU模式只用GPU0。
# 约束：这是Kaggle硬件吞吐，不等于本机延迟；不使用语义测试得分推算速度。
def benchmark(binary, model, name, out):
    for device, layers in (("cpu", "0"), ("gpu", "99")):
        logging.info("benchmark_started arm=%s device=%s", name, device)
        with (out / f"{name}-{device}-bench.json").open("w") as stream, (out / f"{name}-{device}-bench.log").open("w") as log:
            subprocess.run([str(binary), "-m", str(model), "-p", "128", "-n", "32", "-r", "3", "-t", "4", "-ngl", layers, "-sm", "none", "-mg", "0", "-o", "json"], stdout=stream, stderr=log, check=True)
        logging.info("benchmark_completed arm=%s device=%s", name, device)


# 功能：按用户批准的v2方案避免原生GQA自动选择math路径。
# 输入：`module` 注意力层；`query`、`key`、`value`投影张量；`attention_mask`掩码；`dropout`概率；`scaling`缩放；`is_causal`因果标志；`kwargs`框架附加参数。
# 输出：按Transformers布局返回注意力结果和None。
# 逻辑：显式复制KV头，强制EFFICIENT_ATTENTION；首层首次调用记录实际形状、类型和掩码状态。
# 约束：仅支持本实验无缓存、等长因果训练；不支持输出注意力权重；内核不支持时直接失败，禁止math回退。
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


# 功能：在真实T4上核验自定义注意力的前向和反向数值。
# 输入：`out` 审计目录；读取CONFIG中的新实验预检容差。
# 输出：attention-preflight.json；不满足容差或内核不可用则停止。
# 逻辑：32词元、32个Q头/8个KV头与FP32数学参考比较；记录最大绝对误差并验证Q/K/V梯度。
# 约束：容差只用于实现预检，不更改质量评分；随机状态在上下文退出时恢复，不使用测试数据。
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


# 功能：初始化相同种子、量化、LoRA及梯度检查点配置。
# 输入：`snapshot` 本次下载的官方权重；读取CONFIG和GPU0。
# 输出：tokenizer、训练态模型、可训练参数列表。
# 逻辑：通过公开接口注册注意力及官方SDPA掩码；每次初始化重设种子，使诊断不消耗正式训练的随机序列。
# 约束：仅注意力计算路径属于用户批准的v2变更，其他训练条件与v1一致；不自动降级。
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


# 功能：计算无截断的assistant目标交叉熵。
# 输入：`model` 训练模型；`tokenizer`官方分词器；`row`单条训练数据。
# 输出：带计算图的loss和完整token数量。
# 逻辑：完整提示参与注意力，只对目标对应logits计算损失，保留v1的目标权重。
# 约束：前缀不一致、超出6144或非有限loss均失败；不裁剪schema或样本。
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


# 功能：在构建推理工具前验证首条完整样本能完成训练前后向。
# 输入：`snapshot` 官方权重；`rows`冻结训练集；`out`审计目录。
# 输出：memory-preflight.json；不产生优化器更新或适配器权重。
# 逻辑：使用与正式训练同一初始化、样本顺序及缩放损失，记录加载/前向/反向显存；结束释放模型。
# 约束：不缩短样本、不改变参数，不将诊断计入24步；正式训练会从相同种子重新初始化。
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


# 功能：执行一轮固定的单T4 QLoRA训练。
# 输入：`snapshot` 官方HF权重；`rows` 仅训练数据；`out` 输出根目录。
# 输出：LoRA adapter、训练步骤日志和数值/参数审计。
# 逻辑：按v2注意力初始化NF4/r8 LoRA；完整提示参与目标loss，累积4样本更新，共24步；不计入诊断步骤。
# 约束：不裁剪样本，不用测试集选模型；非有限loss/梯度直接失败，无自动重试或降级。
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


# 功能：删除已完成阶段的专属临时制品以控制磁盘占用。
# 输入：`path` 待清理目标；`scratch` 本实验新建临时根目录。
# 输出：无；删除目标但不删除输出或他人缓存。
# 逻辑：验证解析绝对路径严格位于scratch下，且不是scratch本身。
# 约束：只用于本次生成的文件或下载副本，不跨shell、不处理用户业务文件。
def remove_scratch(path, scratch):
    path, root = path.resolve(), scratch.resolve()
    if path == root or root not in path.parents:
        raise ValueError("unsafe_scratch_path")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


# 功能：运行独立微调与量化对照。
# 输入：无函数参数；Kaggle私有数据挂载与T4资源。
# 输出：adapter、微调Q4 GGUF、四组原始预测/指标/微基准、环境和制品摘要；失败保留诊断并退出。
# 逻辑：先做数值及完整样本显存预检，再构建、基线、正式训练；显式限定PyTorch，临时制品仅在独立/tmp目录。
# 约束：不安装替代模型、不恢复失败步骤、不改测试标准；数据和硬件不符直接停止。
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
        # 已加载原权重和tokenizer；只删除本次专属副本，为合并制品腾出空间。
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
