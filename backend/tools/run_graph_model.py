"""职责：为语义图谱输入启动独立的本机 Qwen GGUF 服务。
实现：校验模型文件摘要，只绑定回环地址，使用纯 CPU 和固定别名；服务错误直接退出，无后台重启。
关联：semantic_provider 使用 GRAPH_LLM_URL/GRAPH_LLM_MODEL；独立于冻结 CRMArena 的端口及实验参数。
目录：
- main：验证路径并启动本机 llama.cpp 服务。
变量索引：
- MODEL_HASHES：官方与微调实验Q4制品的已核验摘要；默认仍为官方。
"""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess

MODEL_HASHES = {
    "official": "bb612de21eb450942d3579558302c7bde32dc15d77f8dc8a8fc3f5b0c7256a89",
    "semantic-v2": "cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09",
}


# 功能：启动与本轮协议相匹配的本机语义模型。
# 输入：无函数参数；CLI executable/model必填；variant默认official，port默认8088，threads默认8。
# 输出：子进程服务日志与退出码；不创建可见 Windows 窗口。
# 逻辑：按显式variant校验制品，端口可用后按指定CPU线程启动；上下文16384、单槽位、输出由调用方限制1536。
# 约束：semantic-v2仅为实验候选，不自动替换official；显式禁用内存自动适配和上下文滑动以维持既定上下文；线程用于部署适配，不改冻结实验。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--variant", choices=MODEL_HASHES, default="official")
    parser.add_argument("--threads", type=int, default=8)
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
    print(f"variant={args.variant} model_sha256={digest} alias=salesmate-graph endpoint=http://127.0.0.1:{args.port}/v1", flush=True)
    command = [str(executable), "-m", str(model), "--host", "127.0.0.1", "--port", str(args.port), "--alias", "salesmate-graph",
               "-ngl", "0", "-t", str(args.threads), "-tb", str(args.threads), "-c", "16384", "-np", "1", "--fit", "off", "--cache-ram", "0", "--no-cache-prompt", "--no-context-shift", "--offline"]
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
