"""职责：验证销售关系模型、权限、业务状态和外部动作边界。
实现：邮件草稿使用无预选公司的工作空间会话；隔离测试数据库驱动真实 HTTP 及事务，Google 调用仅在指定测试边界模拟。
关联：覆盖 apps.sales，并验证 crm 原邮件投影兼容性；不证明真实外部授权可用。
目录：
- SalesTests：销售业务集成测试。
- SalesTests.setUp：建立隔离用户、公司及客户端。
- SalesTests.create：通过真实 API 新建记录。
- SalesTests.command：发送版本化业务命令。
- SalesTests.quote：构造含折扣明细的报价草稿。
- SalesTests.action：构造待确认发信动作。
- SalesTests.test_catalog_and_unknown_fields：字段契约和只读保护。
- SalesTests.test_revision_and_ownership：版本及跨用户隔离。
- SalesTests.test_team_sharing_and_private_records：共享业务与私人数据分离。
- SalesTests.test_manager_cannot_escalate：管理角色防提权。
- SalesTests.test_quote_money_freeze_and_projection：金额冻结和真实外发证据。
- SalesTests.test_order_only_confirmed_is_history：订单状态与历史投影。
- SalesTests.test_line_validation_and_parent_revision：行约束及父单据版本。
- SalesTests.test_cross_company_relations_rejected：跨公司引用拒绝。
- SalesTests.test_messages_idempotent_and_immutable：会话幂等与不可变消息。
- SalesTests.test_action_confirmation_success_and_idempotence：明确批准和单次执行。
- SalesTests.test_action_unknown_result_not_retried：网络不明结果不重试。
- SalesTests.test_quote_reserved_by_approved_action：批准动作冻结报价版本。
- SalesTests.test_action_cancel_and_tampering：取消及不可编辑快照。
- SalesTests.test_files_private_and_not_inline：附件隔离和强制下载。
- SalesTests.test_due_notifications_are_deduplicated：到期提醒去重。
- SalesTests.test_manual_alias_controls_ingestion：人工映射控制未来归组。
- SalesTests.test_manual_primary_contact：主要联系人覆盖原自动选择。
- SalesTests.test_merge_preserves_records_and_profiles：合并保持关系及补充资料。
- SalesTests.test_connection_credentials_never_exposed：连接密文不出接口。
- SalesTests.test_missing_vault_fails_explicitly：未配置密钥时明确失败。
变量索引：
- BASE：销售 API 前缀。
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import ingestion, rules, selectors
from apps.crm.models import Contact, Mailbox
from apps.sales import actions, grouping, models, services

BASE = "/api/v1/sales/"


# 功能：验证销售业务的持久化及边界。
# 逻辑：每项测试使用真实隔离数据库，业务更新按现有 provider 创建分析任务；测试不启动独立 Worker。
# 约束：外部 provider 只在动作测试中模拟；不发送真实消息。
class SalesTests(TestCase):
    # 功能：建立最小业务上下文。
    # 输入：无外部参数，测试框架创建。
    # 输出：用户、公司和认证 APIClient 实例状态。
    # 逻辑：两用户两公司用于权限反例。
    # 约束：密码和邮箱都是测试样例。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="seller")
        self.other = get_user_model().objects.create_user(username="colleague")
        self.company = grouping.create_company(self.user, "测试客户")
        self.second = grouping.create_company(self.user, "第二客户")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # 功能：经 HTTP 创建业务记录。
    # 输入：`resource`、`data`、`expected` 预期状态默认 201。
    # 输出：响应数据。
    # 逻辑：失败直接显示真实接口错误便于诊断。
    # 约束：不绕过业务服务。
    def create(self, resource, data, expected=201):
        response = self.client.post(BASE + f"records/{resource}/", data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # 功能：发送版本化命令。
    # 输入：`resource`、`record`、`command`、`value`、`expected` 默认 200。
    # 输出：响应数据。
    # 逻辑：使用读取到的 revision 构造 If-Match。
    # 约束：冲突测试可传旧版本记录。
    def command(self, resource, record, command, value=None, expected=200):
        response = self.client.post(
            BASE + f"records/{resource}/{record['id']}/commands/",
            {"command": command, "value": value},
            format="json",
            HTTP_IF_MATCH=str(record["revision"]),
        )
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # 功能：建立可核验金额的报价。
    # 输入：无外部参数，读取测试公司。
    # 输出：含一行的报价响应。
    # 逻辑：3 × 12.35 − 0.05 = 37.00。
    # 约束：不审核或外发。
    def quote(self):
        quote = self.create(
            "quotes",
            {"company": str(self.company.pk), "number": "Q-001", "currency": "USD"},
        )
        self.create(
            "quote-lines",
            {
                "quote": quote["id"],
                "description": "设备",
                "quantity": "3",
                "unit_price": "12.35",
                "discount": "0.05",
            },
        )
        return self.client.get(BASE + f"records/quotes/{quote['id']}/").data

    # 功能：建立包含完整邮件快照的待确认动作。
    # 输入：`quote` 可选已审核报价响应。
    # 输出：动作响应。
    # 逻辑：草稿使用工作空间会话；连接凭证为不可用占位，实际执行测试必须 patch 凭证边界。
    # 约束：创建不意味着批准或发送。
    def action(self, quote=None):
        connection = models.Connection.objects.create(
            owner=self.user,
            provider="gmail",
            account="seller@example.com",
            encrypted_credentials="test-not-a-token",
        )
        conversation = self.create("conversations", {"title": "工作空间"})
        draft = self.create(
            "drafts",
            {
                "conversation": conversation["id"],
                "kind": "email",
                "subject": "测试报价",
                "content": "请审阅",
                "recipients": ["buyer@example.com"],
            },
        )
        parameters = {"connection_id": str(connection.pk), "draft_id": draft["id"]}
        if quote:
            parameters["quote_id"] = quote["id"]
        return self.create(
            "actions",
            {
                "company": str(self.company.pk),
                "tool": "gmail.send",
                "parameters": parameters,
                "idempotency_key": str(uuid.uuid4()),
            },
        )

    # 功能：验证字段契约和客户端权限字段拒绝。
    # 输入：测试身份与 catalog 请求。
    # 输出：全部资源存在且伪造 owner/status 返回 400。
    # 逻辑：验证实际 API 而非仅模型字段。
    # 约束：不覆盖所有字段组合。
    def test_catalog_and_unknown_fields(self):
        response = self.client.get(BASE + "catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["resources"]), 20)
        self.create(
            "tickets",
            {"company": str(self.company.pk), "title": "测试", "status": "closed"},
            400,
        )
        self.create(
            "products",
            {
                "sku": "P",
                "name": "设备",
                "currency": "USD",
                "unit_price": "10",
                "owner": self.other.pk,
            },
            400,
        )

    # 功能：验证修订冲突及非所有者隔离。
    # 输入：真实创建后使用过期版本和第二用户。
    # 输出：过期 409、越权 404。
    # 逻辑：先成功修改，再复用旧 revision。
    # 约束：不以列表隐藏代替详情权限。
    def test_revision_and_ownership(self):
        ticket = self.create(
            "tickets", {"company": str(self.company.pk), "title": "原始"}
        )
        path = BASE + f"records/tickets/{ticket['id']}/"
        self.assertEqual(
            self.client.patch(
                path, {"title": "新的"}, format="json", HTTP_IF_MATCH="0"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(
                path, {"title": "覆盖"}, format="json", HTTP_IF_MATCH="0"
            ).status_code,
            409,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)

    # 功能：验证共享客户编辑与个人会话隔离。
    # 输入：editor 团队、editor 公司授权和私人会话。
    # 输出：同事可编辑工单，但看不到私人会话和 crm 邮件上下文。
    # 逻辑：使用无公司会话，分别调用业务 API 与原 crm 私有 API。
    # 约束：共享不代表邮箱授权。
    def test_team_sharing_and_private_records(self):
        team = self.create("teams", {"name": "销售团队"})
        self.create(
            "memberships", {"team": team["id"], "user": self.other.pk, "role": "editor"}
        )
        self.create(
            "grants",
            {"company": str(self.company.pk), "team": team["id"], "role": "editor"},
        )
        self.create("conversations", {"title": "工作空间"})
        self.client.force_authenticate(self.other)
        self.create("tickets", {"company": str(self.company.pk), "title": "协作工单"})
        self.assertEqual(
            self.client.get(BASE + "records/conversations/").data["count"], 0
        )
        self.assertEqual(
            self.client.get(f"/api/v1/companies/{self.company.pk}/").status_code, 404
        )
        self.assertNotIn("content", str(self.client.get(BASE + "audit/").data))

    # 功能：验证 manager 不能新增或修改其他管理者。
    # 输入：团队拥有者授予的 manager 身份。
    # 输出：伪造管理权 403。
    # 逻辑：尝试创建 manager 并降级自身。
    # 约束：普通编辑成员管理仍允许。
    def test_manager_cannot_escalate(self):
        team = self.create("teams", {"name": "团队"})
        member = self.create(
            "memberships",
            {"team": team["id"], "user": self.other.pk, "role": "manager"},
        )
        third = get_user_model().objects.create_user(username="third")
        self.client.force_authenticate(self.other)
        self.create(
            "memberships",
            {"team": team["id"], "user": third.pk, "role": "manager"},
            403,
        )
        response = self.client.patch(
            BASE + f"records/memberships/{member['id']}/",
            {"role": "editor"},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 403)

    # 功能：验证报价净额、冻结和实际发送证据。
    # 输入：可核验报价及审批操作。
    # 输出：净额 37.00，审核后禁止编辑、禁止伪造已发送。
    # 逻辑：验证持久化 company.quotes 中没有草稿或仅审核记录。
    # 约束：真实发送另有动作测试。
    def test_quote_money_freeze_and_projection(self):
        quote = self.quote()
        self.assertEqual(quote["total"], "37.00")
        quote = self.command("quotes", quote, "transition", "approved")
        self.company.refresh_from_db()
        self.assertEqual(self.company.quotes, [])
        self.command("quotes", quote, "transition", "sent", 409)
        response = self.client.patch(
            BASE + f"records/quotes/{quote['id']}/",
            {"notes": "changed"},
            format="json",
            HTTP_IF_MATCH=str(quote["revision"]),
        )
        self.assertEqual(response.status_code, 409)

    # 功能：验证订单确认与取消对历史投影的影响。
    # 输入：一个含明细的草稿订单。
    # 输出：草稿不进入历史，确认进入，取消退出有效历史。
    # 逻辑：每次读取真实 Company JSON。
    # 约束：不进行库存或会计收入推断。
    def test_order_only_confirmed_is_history(self):
        order = self.create(
            "orders",
            {"company": str(self.company.pk), "number": "O1", "currency": "USD"},
        )
        self.create(
            "order-lines",
            {
                "order": order["id"],
                "description": "设备",
                "quantity": "2",
                "unit_price": "10",
            },
        )
        order = self.client.get(BASE + f"records/orders/{order['id']}/").data
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders, [])
        order = self.command("orders", order, "transition", "confirmed")
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders[0]["amount"], "20.00")
        self.command("orders", order, "transition", "cancelled")
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders, [])

    # 功能：验证数量折扣边界和父版本递增。
    # 输入：报价和非法明细。
    # 输出：非法折扣 400，父单据 revision 反映已有合法行。
    # 逻辑：拒绝折扣超过数量乘单价。
    # 约束：金额未通过校验不能影响父单据。
    def test_line_validation_and_parent_revision(self):
        quote = self.quote()
        self.assertEqual(quote["revision"], 1)
        self.create(
            "quote-lines",
            {
                "quote": quote["id"],
                "description": "非法",
                "quantity": "1",
                "unit_price": "1",
                "discount": "2",
            },
            400,
        )
        self.assertEqual(models.Quote.objects.get(pk=quote["id"]).revision, 1)

    # 功能：验证报价来源和主要联系人跨客户引用被拒绝。
    # 输入：第二客户及第一客户报价/联系人。
    # 输出：两个写入都返回 400。
    # 逻辑：即使同 owner 也要求公司一致。
    # 约束：不依赖 UUID 难猜保证安全。
    def test_cross_company_relations_rejected(self):
        quote = self.quote()
        self.create(
            "orders",
            {
                "company": str(self.second.pk),
                "number": "O2",
                "currency": "USD",
                "quote": quote["id"],
            },
            400,
        )
        contact = Contact.objects.create(
            company=self.second, email="second@example.com"
        )
        setting = models.CompanySettings.objects.get(company=self.company)
        response = self.client.patch(
            BASE + f"records/customers/{setting.pk}/",
            {"primary_contact": contact.pk},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 400)

    # 功能：验证消息提交幂等、角色不可伪造和不可修改。
    # 输入：会话及相同 client_key 消息。
    # 输出：相同内容返回同 ID，不同内容冲突，编辑与角色伪造失败。
    # 逻辑：工作空间会话内通过事务唯一性与载荷一致性共同保证。
    # 约束：没有模拟或生成助手回复。
    def test_messages_idempotent_and_immutable(self):
        conversation = self.create("conversations", {"title": "工作空间"})
        data = {
            "conversation": conversation["id"],
            "content": "请整理需求",
            "client_key": str(uuid.uuid4()),
        }
        message = self.create("messages", data)
        self.assertEqual(self.create("messages", data)["id"], message["id"])
        self.create("messages", {**data, "content": "other"}, 409)
        self.create("messages", {**data, "role": "assistant"}, 400)
        response = self.client.patch(
            BASE + f"records/messages/{message['id']}/",
            {"content": "改写历史"},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 409)
        self.create(
            "drafts",
            {"conversation": conversation["id"], "kind": "chat", "content": "未完成"},
        )

    # 功能：验证明确确认后只执行一次。
    # 输入：测试草稿与模拟凭证/provider。
    # 输出：未批准不调用 provider，批准后成功，再执行不重复。
    # 逻辑：幂等键重复请求复用原动作。
    # 约束：此测试不连接真实 Gmail。
    def test_action_confirmation_success_and_idempotence(self):
        action = self.action()
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                return_value={"message_id": "mock-id"},
            ) as execute,
        ):
            self.assertEqual(actions.run_action(action["id"]), "pending_confirmation")
            execute.assert_not_called()
            action = self.command("actions", action, "decide", "approved")
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
            execute.assert_called_once()
        duplicate = self.create(
            "actions",
            {
                "company": action["company"],
                "tool": action["tool"],
                "parameters": action["parameters"]["request"],
                "idempotency_key": action["idempotency_key"],
            },
        )
        self.assertEqual(duplicate["id"], action["id"])

    # 功能：验证外部结果不明单独持久化且不重试。
    # 输入：批准动作，provider 模拟连接中断。
    # 输出：uncertain，后续 worker 调用不会再次发送。
    # 逻辑：网络调用开始后异常不能判定为未发送。
    # 约束：不声称模拟异常等于所有真实 Google 错误。
    def test_action_unknown_result_not_retried(self):
        action = self.command("actions", self.action(), "decide", "approved")
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                side_effect=ConnectionError("mock"),
            ) as execute,
        ):
            self.assertEqual(actions.run_action(action["id"]), "uncertain")
            self.assertEqual(actions.run_action(action["id"]), "uncertain")
            execute.assert_called_once()

    # 功能：验证批准外发时保留报价版本。
    # 输入：已审核报价和批准动作。
    # 输出：撤销审核失败，模拟发送成功才产生 actual_outbound。
    # 逻辑：检测网络窗口内状态修改风险。
    # 约束：provider 被模拟，不发送邮件。
    def test_quote_reserved_by_approved_action(self):
        quote = self.command("quotes", self.quote(), "transition", "approved")
        action = self.command("actions", self.action(quote), "decide", "approved")
        self.command("quotes", quote, "transition", "draft", 409)
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                return_value={"message_id": "mock-sent"},
            ),
        ):
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
        self.company.refresh_from_db()
        self.assertEqual(self.company.quotes[0]["evidence_type"], "actual_outbound")

    # 功能：验证动作取消与快照不可编辑。
    # 输入：待确认动作及修改参数请求。
    # 输出：编辑 409，取消后不能批准。
    # 逻辑：所有动作写入经专用入口。
    # 约束：未执行动作没有撤回外部服务的副作用。
    def test_action_cancel_and_tampering(self):
        action = self.action()
        response = self.client.patch(
            BASE + f"records/actions/{action['id']}/",
            {"parameters": {}},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 409)
        action = self.command("actions", action, "decide", "cancelled")
        self.command("actions", action, "decide", "approved", 409)

    # 功能：验证上传存储、下载权限和附件响应。
    # 输入：临时目录和合成 HTML 文件。
    # 输出：owner 可下载，其他用户 404，响应不是内联 HTML。
    # 逻辑：测试存储字节和响应 disposition；耗尽流让测试客户端按自身生命周期关闭响应。
    # 约束：测试结束只清理临时目录。
    def test_files_private_and_not_inline(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(BASE_DIR=Path(directory)),
        ):
            response = self.client.post(
                BASE + "files/",
                {
                    "company": str(self.company.pk),
                    "file": SimpleUploadedFile(
                        "proof.html", b"<h1>test</h1>", content_type="text/html"
                    ),
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn("storage_key", response.data)
            path = BASE + f"files/{response.data['id']}/download/"
            download = self.client.get(path)
            self.assertIn("attachment", download["Content-Disposition"])
            self.assertEqual(b"".join(download.streaming_content), b"<h1>test</h1>")
            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.get(path).status_code, 404)

    # 功能：验证重复轮询不产生重复提醒。
    # 输入：已到期 open 跟进。
    # 输出：首次新增 1，第二次 0，已读状态可保存。
    # 逻辑：负责人、任务、revision 为联合去重键。
    # 约束：不发送外部提醒。
    def test_due_notifications_are_deduplicated(self):
        self.create(
            "follow-ups",
            {
                "company": str(self.company.pk),
                "title": "回访",
                "due_at": timezone.now().isoformat(),
            },
        )
        self.assertEqual(services.notify_due(), 1)
        self.assertEqual(services.notify_due(), 0)
        notification = self.client.get(BASE + "records/notifications/").data["results"][
            0
        ]
        self.assertIsNotNone(
            self.command("notifications", notification, "read")["read_at"]
        )

    # 功能：验证精确联系人映射优先于域名规则。
    # 输入：人工 alias 与规则生成的合成邮件。
    # 输出：新邮件归入指定人工公司。
    # 逻辑：调用实际 ingestion，未改动邮件本体。
    # 约束：不访问外部 Gmail。
    def test_manual_alias_controls_ingestion(self):
        self.create(
            "aliases",
            {
                "company": str(self.company.pk),
                "group_key": "contact:buyer@sample.example",
            },
        )
        mailbox = Mailbox.objects.create(owner=self.user, address="seller@example.com")
        payload = rules.extract_email(
            mailbox, "buyer@sample.example", "询价", "需求：采购", "alias-test"
        )
        self.assertEqual(
            ingestion.submit_emails(self.user, [payload])[0]["company_id"],
            str(self.company.pk),
        )

    # 功能：验证人工主要联系人改变 Grouping 而保留协议。
    # 输入：两个无往来联系人和明确主要联系人设置。
    # 输出：第二联系人被标记主要。
    # 逻辑：通过客户设置 API 保存后读取真实 selector。
    # 约束：未增加 L1–L4 协议字段。
    def test_manual_primary_contact(self):
        Contact.objects.create(company=self.company, email="a@example.com")
        primary = Contact.objects.create(company=self.company, email="z@example.com")
        setting = models.CompanySettings.objects.get(company=self.company)
        response = self.client.patch(
            BASE + f"records/customers/{setting.pk}/",
            {"primary_contact": primary.pk},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 200, response.data)
        grouped, _ = selectors.context_pair(self.company)
        self.assertEqual(
            [c["contact_email"] for c in grouped["contacts"] if c["is_primary"]],
            ["z@example.com"],
        )

    # 功能：验证公司合并保留业务关系和联系人资料。
    # 输入：来源工单、联系人及电话。
    # 输出：工单和资料移入目标，来源归档。
    # 逻辑：真实事务合并后检查关系模型。
    # 约束：不覆盖有冲突的字段。
    def test_merge_preserves_records_and_profiles(self):
        ticket = self.create(
            "tickets", {"company": str(self.company.pk), "title": "来源工单"}
        )
        contact = Contact.objects.create(
            company=self.company, email="buyer@example.com"
        )
        profile = models.ContactProfile.objects.create(
            owner=self.user, contact=contact, phone="123456"
        )
        self.company.refresh_from_db()
        result = grouping.merge_companies(
            self.user,
            self.company.pk,
            self.second.pk,
            self.company.revision,
            self.second.revision,
        )
        self.assertEqual(result["id"], str(self.second.pk))
        self.assertEqual(
            models.Ticket.objects.get(pk=ticket["id"]).company_id, self.second.pk
        )
        profile.refresh_from_db()
        self.assertEqual(profile.contact.company_id, self.second.pk)
        self.assertTrue(
            models.CompanySettings.objects.get(company=self.company).archived
        )

    # 功能：验证连接密文不会进入目录响应。
    # 输入：测试密文连接。
    # 输出：列表含账号但无凭证字段或密文。
    # 逻辑：使用真实连接序列化器。
    # 约束：不解密或验证外部身份。
    def test_connection_credentials_never_exposed(self):
        models.Connection.objects.create(
            owner=self.user,
            provider="gmail",
            account="test@example.com",
            encrypted_credentials="hidden-test-secret",
        )
        response = self.client.get(BASE + "records/connections/")
        self.assertNotIn("hidden-test-secret", str(response.data))
        self.assertNotIn("encrypted_credentials", str(response.data))

    # 功能：验证加密密钥缺失时授权入口明确拒绝。
    # 输入：空 SALESMATE_VAULT_KEY。
    # 输出：409，无连接写入。
    # 逻辑：请求 OAuth 地址即检查服务配置。
    # 约束：不自动生成密钥或降级保存明文。
    @override_settings(SALESMATE_VAULT_KEY="")
    def test_missing_vault_fails_explicitly(self):
        response = self.client.post(
            BASE + "oauth/", {"provider": "gmail"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(models.Connection.objects.count(), 0)
