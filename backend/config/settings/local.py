"""职责：定义仅用于本地开发的配置覆盖。
实现：开启 DEBUG 和本机开发会话自动登录，允许匿名访问 Schema；业务接口仍验证 Session。
关联：由 manage.py、ASGI 和 WSGI 入口默认加载，不用于生产部署。

目录：
- 无

变量索引：
- DEBUG：本地开发开启调试。
- LOCAL_DEBUG_AUTO_LOGIN：是否为回环地址的浏览器自动建立开发会话。
- LOCAL_DEBUG_USER：自动会话所用的既有普通开发用户名。
- SPECTACULAR_SETTINGS：继承基础 Schema 设置并开放本地匿名文档访问。
"""


from .base import *  # noqa: F403

DEBUG = True
LOCAL_DEBUG_AUTO_LOGIN = env.bool("LOCAL_DEBUG_AUTO_LOGIN", default=True)  # noqa: F405
LOCAL_DEBUG_USER = env.str("LOCAL_DEBUG_USER", default="demo")  # noqa: F405
SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,  # noqa: F405
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
}
