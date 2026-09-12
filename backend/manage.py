#!/usr/bin/env python
"""职责：提供 Django 管理命令入口。
实现：设置未显式指定时的本地配置模块，将命令行参数交给 Django 执行；错误按 Django 原行为传播。
关联：使用 config.settings.local；迁移、检查和测试均通过此入口调用框架。

目录：
- main：执行当前进程请求的 Django 管理命令。

变量索引：
- 无
"""


import os
import sys


# 功能：执行当前进程请求的 Django 管理命令。
# 输入：无外部参数；读取进程环境与 sys.argv。
# 输出：正常完成返回 None；命令可能按 Django 行为退出或抛出异常。
# 逻辑：用 setdefault 保留显式配置，再延迟导入框架并传递原始命令行参数。
# 约束：会在未指定时写入 DJANGO_SETTINGS_MODULE；不自动迁移、重试或切换数据库。
def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
