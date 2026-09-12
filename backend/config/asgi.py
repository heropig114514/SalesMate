"""职责：向 ASGI 服务器暴露 Django 应用。
实现：仅在环境变量未设置时选择本地配置，导入时初始化 application；启动异常不吞掉。
关联：由 Uvicorn 等服务器加载，路由及中间件来自 config.settings。

目录：
- 无

变量索引：
- application：供 ASGI 服务器调用的 Django 应用对象。
"""


import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_asgi_application()
