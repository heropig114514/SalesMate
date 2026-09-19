"""职责：验证真实网页侧栏、HTTP、数据库与 Agent 的组合闭环。
实现：Django LiveServerTestCase 创建隔离库及会话，Node 浏览器实际提问，原 Agent 经 HTTP 回报。
关联：browser_chat_live.cjs、tests.integration.test_chat 夹具；模型输出仅在调用边界模拟。
目录：
- ChatBrowserTests：需要显式 Playwright 环境的联合验收。
- ChatBrowserTests.test_browser_agent_round_trip：客户聊天到引用展示完整链路。
- ChatBrowserTests.test_general_browser_round_trip：零客户账户通用聊天、刷新恢复。
- ChatBrowserTests.run_round_trip：两种模式的真实 HTTP 和模型边界协作。
变量索引：
- 无
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.chat import process_chat_once
from apps.chat.models import AnswerRequest
from apps.crm.models import AgentCredential
from tests.integration.test_chat import fixture


# 功能：联合验证网页侧栏与真实后端。
# 逻辑：测试静态根目录指向真实前端资源，客户模式替换启动脚本独立挂载侧栏，通用模式使用完整主页路由，不拦截业务 API。
# 约束：单独通过 manage.py test tools.chat_browser_e2e 运行，必须配置 Playwright；不模拟数据库。
@override_settings(
    STATIC_ROOT=Path(__file__).resolve().parents[1] / "frontend" / "assets"
)
class ChatBrowserTests(LiveServerTestCase):
    # 功能：从网页提交问题并自动显示真实落库回答与来源。
    # 输入：无外部参数，环境提供浏览器运行时，夹具提供合成员工和证据。
    # 输出：浏览器成功退出、数据库 completed 且一条引用。
    # 逻辑：委托共享 run_round_trip 使用客户模式；浏览器写入 pending 后由原 Agent 读取并回报，页面自行轮询显示。
    # 约束：仅模型函数使用 Mock；测试会话 cookie 只经子进程环境传递，不打印或写入仓库。
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_browser_agent_round_trip(self):
        self.run_round_trip(general=False)

    # 功能：验证无客户账号直接聊天及刷新恢复。
    # 输入：无外部参数；运行时提供浏览器路径。
    # 输出：浏览器成功退出及 completed 记录。
    # 逻辑：使用真实主页路由和 API，从空账号创建通用会话。
    # 约束：仅模型输出模拟，不创建客户夹具或拦截业务 API。
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_general_browser_round_trip(self):
        self.run_round_trip(general=True)

    # 功能：协调浏览器提交和真实 Agent 消费。
    # 输入：`general` 决定空客户账号或客户证据夹具。
    # 输出：断言持久化回答、引用数量与浏览器结果。
    # 逻辑：等待 pending 后通过 HTTP 领取与回报，浏览器观察完成。
    # 约束：模型仅调用 Mock，cookie 只通过子进程环境传递，不打印。
    def run_round_trip(self, general):
        self.assertTrue(
            os.environ.get("SALESMATE_PLAYWRIGHT_MODULE"),
            "需要显式 Playwright 模块路径。",
        )
        self.assertTrue(
            os.environ.get("SALESMATE_BROWSER_PATH"), "需要显式浏览器路径。"
        )
        if general:
            owner = get_user_model().objects.create_user(
                username="general-browser-owner"
            )
            company = None
            AgentCredential.objects.create(
                owner=owner,
                name="chat-test",
                digest=hashlib.sha256(b"chat-test-token").hexdigest(),
            )
        else:
            owner, _, company, _ = fixture()
        browser_session = APIClient()
        browser_session.force_login(owner)
        environment = {
            **os.environ,
            "CHAT_TEST_URL": self.live_server_url,
            "CHAT_TEST_SESSION": browser_session.cookies[
                settings.SESSION_COOKIE_NAME
            ].value,
            "CHAT_TEST_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "CHAT_TEST_COMPANY": str(company.pk) if company else "",
            "CHAT_TEST_GENERAL": "1" if general else "0",
        }
        process = subprocess.Popen(
            ["node", str(Path(__file__).with_name("browser_chat_live.cjs"))],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + 25
            while not AnswerRequest.objects.filter(
                owner=owner, status="pending"
            ).exists():
                if time.monotonic() >= deadline and process.poll() is None:
                    process.terminate()
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail(
                        "浏览器未创建待回答请求：" + process.communicate(timeout=5)[0]
                    )
                time.sleep(0.1)
            backend = DjangoBackendClient(
                self.live_server_url + "/api/v1/agent/", "chat-test-token"
            )
            citation = {
                "source_id": "email:seller@chat.example:one:review:0",
                "source_type": "customer_email",
                "title_or_label": "采购需求",
            }
            provider = Mock(
                return_value=json.dumps(
                    {
                        "assistant_text": (
                            "你好，我们可以一起起草邮件。"
                            if general
                            else "客户需要设备。[1]"
                        ),
                        "citations": [] if general else [citation],
                    },
                    ensure_ascii=False,
                )
            )
            result = process_chat_once(backend=backend, chat_provider=provider)
            self.assertEqual(result["status"], "completed", result)
            output, _ = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, output)
            self.assertIn("Live chat browser round trip passed", output)
            provider.assert_called_once()
            request = AnswerRequest.objects.get(owner=owner)
            self.assertEqual(request.status, "completed")
            self.assertEqual(request.citations.count(), 0 if general else 1)
            self.assertEqual(request.company_id, None if general else company.pk)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
