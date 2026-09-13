#!/usr/bin/env python
"""职责：提供 Django 管理命令入口。
实现：加入仓库根目录以加载同仓 Agent，设置默认本地配置，再将命令行参数交给 Django。
关联：使用 config.settings.local；crm_worker 依赖同仓 agent 包，迁移、检查和测试均从此入口加载。

目录：
- main：执行当前进程请求的 Django 管理命令。

变量索引：
- 无
"""


import os
import sys
from pathlib import Path


# 功能：执行当前进程请求的 Django 管理命令。
# 输入：无外部参数；读取进程环境与 sys.argv。
# 输出：正常完成返回 None；命令可能按 Django 行为退出或抛出异常。
# 逻辑：repository_root 根据本文件定位同仓 Agent，按需加入导入路径；保留显式配置后传递命令参数。
# 约束：修改当前进程 sys.path；仅在未指定时写入配置环境变量，不自动迁移、重试或切换数据库。
def main():
    repository_root = str(Path(__file__).resolve().parent.parent)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
