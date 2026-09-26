"""职责：通过真实HTTP工具接口验证隔离图谱及一次模型推理。
实现：显式凭证文件、唯一合成来源、逐项结果落盘；不重试失败的生成。
关联：graph工具调用现有后端和已配置本机模型；不得指向真实业务账号。
目录：
- main：执行鉴权、schema、不完整输入、幂等和自然语言冒烟。
变量索引：
- 无
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit
import requests


# 功能：使用合成身份验证实际API与模型。
# 输入：无函数参数；CLI url、credential-file、output必填；URL仅允许回环HTTP。
# 输出：每次响应及verification.json；失败保留已取得结果并非零退出。
# 逻辑：一次自然文本调用，验证正向needs_product和未知预算不推断；HTTP状态与端到端时间分别记录。
# 约束：会在专用沙盒身份写入合成观察；不删数据、不重试、不更改模型参数；只证明此样例。
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
