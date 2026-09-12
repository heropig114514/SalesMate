"""职责：向 WSGI 服务器暴露 Django 应用。
实现：仅在环境变量未设置时选择本地配置，导入时初始化 application；启动异常向调用方传播。
关联：使用 config.settings 的路由、中间件和应用配置。

目录：
- 无

变量索引：
- application：供 WSGI 服务器调用的 Django 应用对象。
"""


import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_wsgi_application()
