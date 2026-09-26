"""Responsibility: Provide a structured business-tool CLI.
Implementation: list/describe/call read arguments from JSON files, write results to stdout, and errors to stderr.
Relationships: client uses independent user authorization; neither MCP nor CLI implements business rules.
Directory:
- main: Parse commands and perform one operation.
Variable index:
- None
"""

import argparse
import json
from pathlib import Path
import sys
from .client import ToolClient, ToolError


# Function: Execute the CLI.
# Inputs: `argv` is an optional argument sequence; reads dedicated environment configuration.
# Outputs: Exit code and JSON.
# Logic: Writes require a user-supplied key; catalogs retain pagination.
# Constraints: Never accept tokens on the command line or embed bodies in shell commands; file/protocol errors return nonzero.
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
