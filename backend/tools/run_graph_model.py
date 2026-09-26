"""Responsibility: Start a separate local Qwen GGUF service for semantic-graph input.
Implementation: Validates the model digest, binds only to loopback, and uses CPU according to explicit inference configuration; retains baseline by default and exits on error without restarting.
Relationships: semantic_provider uses GRAPH_LLM_URL and GRAPH_LLM_MODEL; this entry point validates model digests and service parameters.
Directory:
- main: Validate paths and start the local llama.cpp service.
Variable index:
- MODEL_HASHES: Digests for the retained semantic-v2 Q4 artifact; this artifact is the only deployment option.
"""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.knowledge_graph.inference_profiles import PROFILES, server_arguments

MODEL_HASHES = {
    "semantic-v2": "cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09",
}


# Function: Start the local semantic model matching this run's protocol.
# Inputs: No function parameters; CLI executable and model are required, variant defaults to semantic-v2, profile to baseline, port to 8088, and threads to 8.
# Outputs: Child-service logs and exit code; does not create a visible Windows window.
# Logic: Validates the artifact by variant, then builds CPU arguments by profile after the port is available; baseline retains a 16384-token context and callers limit output to 1536.
# Constraints: Optimized configuration requires explicit selection and agreement with backend GRAPH_LLM_PROFILE; no automatic adaptation, truncation, or fallback, and training and prior evaluations remain unchanged.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--variant", choices=MODEL_HASHES, default="semantic-v2")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--profile", choices=PROFILES, default="baseline")
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("threads must be positive")
    executable, model = args.executable.resolve(strict=True), args.model.resolve(strict=True)
    with model.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != MODEL_HASHES[args.variant]:
        raise ValueError("Qwen model artifact hash mismatch")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    print(f"variant={args.variant} profile={args.profile} model_sha256={digest} alias=salesmate-graph endpoint=http://127.0.0.1:{args.port}/v1", flush=True)
    command = [str(executable), "-m", str(model), "--host", "127.0.0.1", "--port", str(args.port), "--alias", "salesmate-graph",
               *server_arguments(args.profile, args.threads)]
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        code = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=30)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
