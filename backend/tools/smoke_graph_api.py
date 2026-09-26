"""Responsibility: Verify an isolated graph and one model inference through the real HTTP tool interface.
Implementation: Uses an explicit credential file, a unique synthetic source, and per-step results persisted to disk; failed generation is not retried.
Relationships: Graph tools call the existing backend and configured local model; they must not target a real business account.
Directory:
- main: Run authentication, schema, incomplete-input, idempotency, and natural-language smoke checks.
Variable index:
- None
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit
import requests


# Function: Verify the real API and model using a synthetic identity.
# Inputs: No function parameters; CLI url, credential-file, and output are required; URL permits loopback HTTP only.
# Outputs: Each response and verification.json; failures retain obtained results and exit nonzero.
# Logic: Makes one natural-text call, verifies positive needs_product and that an unknown budget is not inferred, and records HTTP status separately from end-to-end time.
# Constraints: Writes synthetic observations under the dedicated sandbox identity; does not delete data, retry, or change model parameters; proves this example only.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.username or url.password or url.query or url.fragment:
        raise ValueError("Use an explicit loopback HTTP endpoint or SSH tunnel")
    credential = json.loads(args.credential_file.read_text(encoding="utf-8"))
    if not credential["username"].startswith("graph-sandbox-"):
        raise ValueError("Only a dedicated graph-sandbox- identity is allowed")
    args.output.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.trust_env = False
    anonymous = session.get(args.url.rstrip("/") + "/api/v1/agent-tools/catalog/", timeout=15, allow_redirects=False)
    assert anonymous.status_code == 401, "Anonymous tool request must be rejected"
    session.headers["Authorization"] = "Tool " + credential["token"]
    now = datetime.now(timezone.utc).isoformat()
    key = "deploy-smoke-" + uuid.uuid4().hex
    structured = {"source_key": key + "-records", "observed_at": now, "records": [
        {"key": "company", "schema": "crm.company", "fields": {"name": "SandboxAcme"}},
        {"key": "product", "schema": "sales.product", "fields": {"name": "SandboxEdge"}}]}
    calls = [("schema", "graph.schema", {}), ("records", "graph.ingest", structured),
             ("replay", "graph.ingest", structured), ("text", "graph.ingest", {
                 "source_key": key + "-text", "observed_at": now,
                 "text": "SandboxAcme needs SandboxEdge. The budget is unknown."}),
             ("facts", "graph.facts", {}), ("status", "graph.status", {})]
    results = {}
    for label, name, payload in calls:
        started = time.monotonic()
        print(f"START {label} tool={name}", flush=True)
        response = session.post(args.url.rstrip("/") + "/api/v1/agent-tools/call/",
                                json={"name": name, "arguments": payload}, timeout=(10, 360), allow_redirects=False)
        result = {"status_code": response.status_code, "seconds": time.monotonic() - started, "body": response.json()}
        (args.output / (label + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"END {label} status={response.status_code} seconds={result['seconds']:.3f}", flush=True)
        assert response.status_code == 200, f"{label} failed; inspect saved response"
        results[label] = result
    assert len(results["schema"]["body"]["data"]["schemas"]) == 48
    assert results["records"]["body"]["data"]["id"] == results["replay"]["body"]["data"]["id"]
    extraction = results["text"]["body"]["data"]["extraction"]
    entities = {entity["key"]: entity for entity in extraction["entities"]}
    facts = extraction["facts"]
    assert any(fact["predicate"] == "needs_product" and entities[fact["subject"]]["kind"] == "crm.company"
               and entities[fact["object"]]["kind"] == "sales.product" for fact in facts), "Missing or reversed product need"
    assert not any(fact["predicate"] == "reported_budget" for fact in facts), "Unknown budget must not become a fact"
    assert results["status"]["body"]["data"]["current"]
    summary = {"passed": True, "anonymous_status": anonymous.status_code, "schema_count": 48, "model_calls": 1,
               "model_seconds": results["text"]["body"]["data"]["model_audit"]["seconds"],
               "http_seconds": results["text"]["seconds"], "source_key": key,
               "limitation": "One synthetic smoke case, not production quality or load testing"}
    (args.output / "verification.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
