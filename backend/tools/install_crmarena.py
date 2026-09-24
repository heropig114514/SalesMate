"""职责：安装已核验的 CRMArena v3 Kaggle 制品到后端独立目录。
实现：按项目内固定清单校验全部来源文件，再复制并复核；拒绝覆盖已有非完整安装。
关联：crmarena 使用此目录；不下载模型、不访问业务数据库。
目录：
- digest：文件摘要。
- install：执行固定清单安装。
- main：命令行入口。
变量索引：
- TARGET：项目内制品目录及可信清单位置。
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

TARGET = Path(__file__).resolve().parents[1] / "model_store" / "crmarena-pro-b2b-v3"


# 功能：计算安装文件摘要。
# 输入：`path` 为源或目标文件。
# 输出：SHA256 字符串。
# 逻辑：只读流式计算。
# 约束：文件访问失败传播。
def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# 功能：安装明确版本的公开制品。
# 输入：`source` 为已下载 results 目录，`target` 默认为固定 TARGET。
# 输出：已安装目录；缺失、摘要不符或已有部分制品时抛异常。
# 逻辑：全部源文件通过校验后复制；已完整安装且校验通过时只验证，不改写。
# 约束：不更改可信清单、不覆盖损坏安装、不自动删除失败时留下的部分文件。
def install(source, target=TARGET):
    source, target = Path(source), Path(target)
    expected = json.loads((target / "manifest.json").read_text(encoding="utf8"))["files"]
    for name, value in expected.items():
        if Path(name).name != name or digest(source / name) != value:
            raise ValueError("Source checksum or filename mismatch: " + name)
    if any((target / name).exists() for name in expected):
        if not all((target / name).is_file() and digest(target / name) == value for name, value in expected.items()):
            raise FileExistsError("Partial or different installation; refusing to overwrite")
        return target
    for name, value in expected.items():
        shutil.copyfile(source / name, target / name)
        if digest(target / name) != value:
            raise ValueError("Installed checksum mismatch: " + name)
    return target


# 功能：从命令行安装明确来源。
# 输入：无函数参数；读取必填 --source。
# 输出：安装目录及成功消息；异常直接失败。
# 逻辑：不自动发现多个版本或切换来源。
# 约束：不修改环境配置或启动服务。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    print("CRMArena artifacts verified:", install(args.source))


if __name__ == "__main__":
    main()
