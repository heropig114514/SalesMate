"""职责：提供结构化业务工具命令行入口。
实现：list/describe/call；参数从 JSON 文件读取，结果到 stdout，错误到 stderr。
关联：client 使用独立用户授权；MCP 与 CLI 不维护业务实现。
目录：
- main：解析命令并执行一次操作。
变量索引：
- 无
"""

import argparse
import json
from pathlib import Path
import sys
from .client import ToolClient, ToolError


# 功能：执行 CLI。
# 输入：`argv` 可选命令行和专用环境配置。
# 输出：退出码及 JSON。
# 逻辑：写调用要求用户提供 key，目录保留分页。
# 约束：不从命令行接收 token、不把正文嵌入 shell；文件与协议错误返回非零。
def main(argv=None):
    parser = argparse.ArgumentParser(description="SalesMate 业务工具")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--category")
    listing.add_argument("--page", type=int, default=1)
    describing = commands.add_parser("describe")
    describing.add_argument("name")
    calling = commands.add_parser("call")
    calling.add_argument("name")
    calling.add_argument("--arguments-file", type=Path, required=True)
    calling.add_argument("--idempotency-key")
    args = parser.parse_args(argv)
    try:
        client = ToolClient.from_env()
        if args.command == "list":
            result = client.catalog(page=args.page, category=args.category)
        elif args.command == "describe":
            result = client.describe(args.name)
        else:
            arguments = json.loads(args.arguments_file.read_text(encoding="utf-8-sig"))
            result = client.call(args.name, arguments, args.idempotency_key)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ToolError, OSError, ValueError) as error:
        print(
            (
                str(error)
                if isinstance(error, ToolError)
                else "输入文件不可读或 JSON 无效。"
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
