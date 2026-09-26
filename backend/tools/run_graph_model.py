"""职责：为语义图谱输入启动独立的本机 Qwen GGUF 服务。
实现：校验模型摘要，只绑定回环地址，按显式推理配置使用CPU；默认保留baseline，错误退出而不重启。
关联：semantic_provider 使用 GRAPH_LLM_URL/GRAPH_LLM_MODEL；模型摘要与服务参数由本入口校验。
目录：
- main：验证路径并启动本机 llama.cpp 服务。
变量索引：
- MODEL_HASHES：保留的semantic-v2 Q4制品摘要；该制品是唯一部署选项。
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


# 功能：启动与本轮协议相匹配的本机语义模型。
# 输入：无函数参数；CLI executable/model必填；variant默认semantic-v2，profile默认baseline，port默认8088，threads默认8。
# 输出：子进程服务日志与退出码；不创建可见 Windows 窗口。
# 逻辑：按variant校验制品，端口可用后按profile构造CPU参数；baseline保持16384上下文，输出由调用方限制1536。
# 约束：优化配置须显式选择且与后端GRAPH_LLM_PROFILE一致；无自动适配、截断或回退，不改训练与旧评测。
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
