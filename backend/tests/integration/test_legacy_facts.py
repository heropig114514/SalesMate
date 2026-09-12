"""职责：验证旧规则数据升级后仍能进入当前分析链路。
实现：在隔离 PostgreSQL 测试库构造旧抽取，运行迁移函数并验证审计保留、幂等和最新版本边界。
关联：0004 迁移、selectors 邮箱投影及 rules L2/L3/L4；不调用 Gmail 或模型。
目录：
- LegacyFactTests：验证历史格式升级与当前业务衔接。
- LegacyFactTests.setUp：建立旧版事实和缺少邮箱传输字段的邮件。
- LegacyFactTests.test_upgrade_preserves_history_and_analysis：验证追加转换版本后完整分析可运行。
- LegacyFactTests.test_newer_extraction_is_not_replaced：验证已有新抽取不被旧格式迁移覆盖。
变量索引：
- migration：0004 模块，测试与 Django migrate 使用同一转换函数。
"""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase

from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.models import Analysis, Company, Email, Extraction, Mailbox

migration = import_module("apps.crm.migrations.0004_legacy_fact_groups")


# 功能：验证历史格式升级与当前业务衔接。
# 逻辑：真实测试数据库承载旧版 JSON，每个测试事务独立回滚。
# 约束：只构造合成规则记录，不评价模型质量。
class LegacyFactTests(TestCase):
    # 功能：建立旧版事实和缺少邮箱传输字段的邮件。
    # 输入：测试框架实例状态，无外部参数。
    # 输出：user、mailbox、email、old、original_facts、original_payload 和 initial_revision。
    # 逻辑：先经正式入库服务建立关系，再精确还原迁移前单值 JSON 与旧邮件本体。
    # 约束：旧版构造只在测试中写入，生产使用冻结的历史迁移函数。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="migration-user")
        self.mailbox = Mailbox.objects.create(owner=self.user, address="sales@internal.example")
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "采购", "需求：设备\n预算：20 万")
        ingestion.submit_emails(self.user, [payload])
        self.email = Email.objects.get()
        self.old = self.email.extractions.get()
        facts = deepcopy(self.old.facts)
        facts["intent_evidence"] = facts.pop("intent_evidences")[0]
        for field in migration.FACT_FIELDS:
            groups = facts[field]
            facts[field] = {"value": groups[0]["value"], "evidence": groups[0]["evidences"][0]} if groups else {"value": None, "evidence": None}
        self.old.prompt_version, self.old.facts = "rules-extract-v1", facts
        self.old.save(update_fields=["prompt_version", "facts"])
        self.email.payload.pop("mailbox_address")
        self.email.save(update_fields=["payload"])
        self.original_facts = deepcopy(facts)
        self.original_payload = deepcopy(self.email.payload)
        self.initial_revision = self.email.company.revision

    # 功能：验证追加转换版本后完整分析可运行。
    # 输入：旧版成功抽取与模拟 schema_editor 的真实数据库连接。
    # 输出：审计完整、单次版本递增、重复调用无变更和规则分析成功的断言。
    # 逻辑：迁移后按真实 selector、Job 和结果服务处理，覆盖原浏览器 500 的完整路径。
    # 约束：schema_editor 仅使用 connection.alias；未模拟数据库、事实或分析结果。
    def test_upgrade_preserves_history_and_analysis(self):
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.old.refresh_from_db()
        self.email.refresh_from_db()
        self.assertEqual(self.old.facts, self.original_facts)
        self.assertEqual(self.email.payload, self.original_payload)
        company = Company.objects.get()
        self.assertEqual(company.revision, self.initial_revision + 1)
        current = selectors.email_data(self.email)
        self.assertEqual(current["mailbox_address"], self.mailbox.address)
        self.assertEqual(current["facts"]["budget"], [{"value": "20 万", "evidences": ["预算：20 万"]}])
        self.assertEqual(current["facts"]["quantity"], [])
        self.assertEqual(current["extract_prompt_version"], migration.CONVERTED_VERSION)
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Extraction.objects.count(), 2)
        company.refresh_from_db()
        self.assertEqual(company.revision, self.initial_revision + 1)
        with transaction.atomic():
            jobs.enqueue(company, "manual_request")
        rules.run_company(self.user, company.pk)
        self.assertEqual(Analysis.objects.count(), 1)
        self.assertTrue(Analysis.objects.get().scores.exists())

    # 功能：验证已有新抽取不被旧格式迁移覆盖。
    # 输入：同一邮件在旧抽取之后已有新协议成功版本。
    # 输出：抽取数量、当前版本和 revision 均保持不变的断言。
    # 逻辑：迁移必须以单调 ID 判断当前版本，不能按版本名称或旧记录匹配强制覆盖。
    # 约束：测试中的新事实来自确定性规则，不调用外部模型。
    def test_newer_extraction_is_not_replaced(self):
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "采购", "需求：设备\n预算：30 万")
        newer = Extraction.objects.create(email=self.email, prompt_version="extract-v6", status="completed", facts=payload["facts"])
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Extraction.objects.count(), 2)
        self.assertEqual(selectors.latest_extraction(self.email).pk, newer.pk)
        self.assertEqual(Company.objects.get().revision, self.initial_revision)
