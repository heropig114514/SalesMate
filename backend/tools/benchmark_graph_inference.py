"""Responsibility: Compare local inference configurations reproducibly for fixed semantic-graph weights.
Implementation: Verifies frozen corpus and weights, starts an independent service per configuration, and saves raw responses, quality, cold/hot latency, and memory samples.
Relationships: inference_profiles defines optimization boundaries and the original fine-tuning score provides the same evaluation; does not modify training or test files.
Directory:
- memory_sampler: Sample resident memory for the service process and its children.
- evaluate_profile: Execute one independent configuration and an explicit cached repeat request.
- main: Validate inputs, freeze this run's plan, and execute all declared configurations.
Variable index:
- SCREEN_FAMILIES: Preselected development-set screening scenarios.
- MODEL_SHA256: Digest of the only fine-tuned Q4 artifact permitted for this run.
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


# Function: Record peak resident memory for the real service process tree.
# Inputs: `pid` is the root service process; `stop` is the termination event; `samples` is the shared result list.
# Outputs: Appends timestamps and RSS bytes every two seconds and ends when the process exits.
# Logic: The official Windows service can spawn an execution process, so child processes are counted too.
# Constraints: Summed RSS can double-count shared pages and is not exclusive physical memory; does not collect credentials from process environments or commands.
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


# Function: Measure one fixed inference configuration.
# Inputs: `args` is validated CLI configuration, `name` is the configuration name, `rows` are explicit samples, `contract` is frozen scoring, and `schema` is the decoding contract.
# Outputs: Summary dictionary and per-sample raw JSON; transport or service failure raises while preserving evidence.
# Logic: Immediately repeats the first cold-start sample to measure caching, then processes subsequent distinct samples; the repeat is excluded from quality summaries.
# Constraints: Model maximum output is 1536, temperature 0, seed 2026, and timeout 300 seconds; format errors count as failures and are not repaired or retried.
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


# Function: Freeze and run development screening or the full test comparison.
# Inputs: No function parameters; CLI specifies executable, model, data, output, profiles, and split; threads defaults to 8 and port to 18088.
# Outputs: protocol.json, comparison.json, and artifacts for every independent configuration; failure of any configuration makes the final exit nonzero.
# Logic: Development uses only three fixed scenarios and test uses all 12; verifies every hash first, records failed configurations, then continues other predeclared independent experiments.
# Constraints: Does not select or rewrite test samples or automatically change configuration from test results; prior Kaggle experiments remain completely unchanged.
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
