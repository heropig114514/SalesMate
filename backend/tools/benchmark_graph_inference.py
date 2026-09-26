"""职责：对固定语义图谱权重进行可复现的本机推理配置对照。
实现：核验冻结语料/权重，逐配置独立启动服务；保存原始响应、质量、冷/热时延和内存采样。
关联：inference_profiles定义优化边界，原微调score提供相同评价；不修改训练/测试文件。
目录：
- memory_sampler：采样服务进程及子进程驻留内存。
- evaluate_profile：执行一个独立配置及明确的缓存重复请求。
- main：校验输入并冻结本轮计划后执行所有声明配置。
变量索引：
- SCREEN_FAMILIES：预先指定的开发集筛选场景。
- MODEL_SHA256：本轮唯一允许的微调Q4制品摘要。
"""
import argparse
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import threading
import time
import traceback
import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.knowledge_graph.inference_profiles import get_profile, prepare_messages, server_arguments
from tools.semantic_finetune_kernel import digest, load_contract, save, score

SCREEN_FAMILIES = ("need_budget", "contact_not_employee", "negated_need")
MODEL_SHA256 = "cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09"


# 功能：记录实际服务进程树的驻留内存峰值。
# 输入：`pid` 根服务进程；`stop` 终止事件；`samples` 共享结果列表。
# 输出：每两秒追加时间戳与RSS字节；进程退出时结束。
# 逻辑：Windows官方服务可派生执行进程，故同时计入子进程。
# 约束：RSS总和可能重复计算共享页，不等于物理内存独占；不采集进程环境或命令中的凭据。
def memory_sampler(pid, stop, samples):
    while not stop.is_set():
        try:
            root = psutil.Process(pid)
            rss = sum(p.memory_info().rss for p in [root, *root.children(recursive=True)] if p.is_running())
            samples.append({"time": time.time(), "rss_bytes": rss})
        except psutil.NoSuchProcess:
            return
        if stop.wait(2):
            return


# 功能：测量一个固定推理配置。
# 输入：`args` 已校验CLI配置，`name` 配置名，`rows` 明确样本，`contract` 冻结评分，`schema` 解码契约。
# 输出：汇总字典及逐样本原始JSON；传输/服务失败抛异常且保留现场。
# 逻辑：首样本冷启动后立即重复以测缓存，再处理后续不同样本；重复不计质量汇总。
# 约束：模型最大输出1536、温度0、种子2026、超时300秒；格式错误计失败，不修复或重试。
def evaluate_profile(args, name, rows, contract, schema):
    out = args.output / name
    out.mkdir()
    profile = get_profile(name)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    command = [str(args.executable), "-m", str(args.model), "--host", "127.0.0.1", "--port", str(args.port),
               "--alias", "salesmate-graph", *server_arguments(name, args.threads)]
    save(out / "command.json", {"args": command, "profile": profile})
    samples, results = [], []
    stopped = threading.Event()
    with (out / "server.log").open("w", encoding="utf-8") as log, requests.Session() as session:
        session.trust_env = False
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        watcher = threading.Thread(target=memory_sampler, args=(process.pid, stopped, samples), daemon=True)
        watcher.start()
        try:
            endpoint = f"http://127.0.0.1:{args.port}"
            deadline = time.monotonic() + 180
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Server exited before readiness; inspect server.log")
                try:
                    ready = session.get(endpoint + "/health", timeout=3)
                    if ready.status_code == 200:
                        break
                except requests.ConnectionError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Server readiness exceeded 180 seconds")
                time.sleep(1)
            props = session.get(endpoint + "/props", timeout=5)
            props.raise_for_status()
            save(out / "props.json", props.json())
            schedule = [(rows[0], "cold"), (rows[0], "repeat"), *[(row, "subsequent") for row in rows[1:]]]
            for index, (row, phase) in enumerate(schedule):
                payload = {"model": "salesmate-graph", "messages": prepare_messages(row["messages"], profile),
                           "temperature": 0, "seed": 2026, "max_tokens": 1536, "stream": False,
                           "cache_prompt": profile["cache"], "response_format": {"type": "json_schema",
                           "json_schema": {"name": "crm_graph_observation", "strict": True, "schema": schema}}}
                save(out / f"{index:02d}-request.json", {"id": row["id"], "phase": phase, "payload": payload})
                print(f"START profile={name} sample={row['id']} phase={phase}", flush=True)
                started = time.monotonic()
                response = session.post(endpoint + "/v1/chat/completions", json=payload, timeout=(10, 300), allow_redirects=False)
                elapsed = time.monotonic() - started
                raw = response.json()
                save(out / f"{index:02d}-raw.json", {"id": row["id"], "phase": phase, "seconds": elapsed,
                                                   "status_code": response.status_code, "response": raw})
                response.raise_for_status()
                choice = raw["choices"][0]
                if choice["finish_reason"] != "stop" or raw["model"] != "salesmate-graph":
                    raise ValueError("Model identity or generation completion mismatch")
                try:
                    parsed = json.loads(choice["message"]["content"])
                except json.JSONDecodeError:
                    parsed = None
                metric = score(parsed, row, contract)
                result = {"id": row["id"], "phase": phase, "seconds": elapsed,
                          "usage": raw.get("usage"), "timings": raw.get("timings"), **metric}
                results.append(result)
                save(out / "progress.json", results)
                print(f"END profile={name} phase={phase} seconds={elapsed:.2f} valid={metric['valid']} exact={metric['facts_exact']} cached={raw.get('timings', {}).get('cache_n')}", flush=True)
        finally:
            process.terminate()
            process.wait(timeout=30)
            stopped.set()
            watcher.join(timeout=5)
            save(out / "memory.json", samples)
    unique = [row for row in results if row["phase"] != "repeat"]
    counts = {key: sum(row[key] for row in unique) for key in ("tp", "fp", "fn", "valid", "facts_exact", "raw_identity_exact", "resolved_identity_exact")}
    denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
    summary = {"profile": name, "config": profile, "n": len(unique), **counts,
               "fact_micro_f1": 2 * counts["tp"] / denominator if denominator else 1,
               "mean_seconds": sum(row["seconds"] for row in unique) / len(unique),
               "cold_seconds": results[0]["seconds"], "repeat_seconds": results[1]["seconds"],
               "peak_sampled_rss_bytes": max(row["rss_bytes"] for row in samples), "results": results}
    save(out / "summary.json", summary)
    return summary


# 功能：冻结并运行开发筛选或完整测试对照。
# 输入：无函数参数；CLI指定executable/model/data/output/profiles/split，threads默认8，port默认18088。
# 输出：protocol.json、comparison.json及各独立配置制品；任一配置失败最终非零退出。
# 逻辑：dev仅固定三场景，test全部12场景；所有哈希先核验，失败配置记录后继续其他预声明独立实验。
# 约束：不挑选或重写测试样本，不自动根据测试结果改配置；旧Kaggle试验完全不变。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profiles", nargs="+", required=True)
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--port", type=int, default=18088)
    args = parser.parse_args()
    for name in args.profiles:
        get_profile(name)
    args.executable = args.executable.resolve(strict=True)
    args.model = args.model.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    if digest(args.model) != MODEL_SHA256:
        raise ValueError("Frozen tuned Q4 checksum mismatch")
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        if digest(args.data / name) != expected:
            raise ValueError("Frozen corpus checksum mismatch: " + name)
    rows = [json.loads(line) for line in (args.data / (args.split + ".jsonl")).read_text(encoding="utf-8").splitlines()]
    if args.split == "dev":
        rows = [row for row in rows if row["family"] in SCREEN_FAMILIES]
    save(args.output / "protocol.json", {"created_at_unix": time.time(), "platform": platform.platform(),
         "processor": platform.processor(), "threads": args.threads, "model_sha256": MODEL_SHA256,
         "corpus_manifest": manifest, "split": args.split, "sample_ids": [row["id"] for row in rows],
         "profiles": {name: get_profile(name) for name in args.profiles}, "seed": 2026, "temperature": 0,
         "max_output_tokens": 1536, "http_timeout_seconds": 300, "schema_policy": "all 48 retained; no truncation",
         "repeat_policy": "repeat first sample once; exclude repeat from quality totals",
         "binary_version": subprocess.check_output([str(args.executable), "--version"], stderr=subprocess.STDOUT, text=True),
         "psutil_version": psutil.__version__, "limitations": "Small synthetic shared-template samples; desktop load not isolated"})
    contract = load_contract(args.data)
    schema = json.loads((args.data / "response-schema.json").read_text(encoding="utf-8"))
    summaries, failures = [], []
    for name in args.profiles:
        try:
            summaries.append(evaluate_profile(args, name, rows, contract, schema))
        except Exception as exc:
            failure = {"profile": name, "error_type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
            save(args.output / name / "failure.json", failure)
            failures.append(failure)
            print(f"FAILED profile={name} error={type(exc).__name__}; no retry or fallback", flush=True)
    save(args.output / "comparison.json", {"summaries": summaries, "failures": failures})
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
