"""职责：验证公司列表批量查询的业务等价性和查询规模。
实现：隔离 PostgreSQL 合成公司、邮件、版本与评分，比较独立投影和批量结果，并验证规模增长时查询数有界。
关联：crm.selectors 的列表与详情共用投影；不访问模型、邮箱或线上数据库。
目录：
- CompanyListTests：验证批量列表契约。
- CompanyListTests.setUp：创建两个归属与测试邮箱。
- CompanyListTests.company：生成有分析和评分的合成公司。
- CompanyListTests.test_batch_matches_independent_projection：核对完整行与邮件、联系人版本语义。
- CompanyListTests.test_query_count_does_not_grow_per_company：验证两家公司与三十家公司的查询预算。
- CompanyListTests.test_filter_page_and_stats_preserve_global_scope：验证筛选、全局排序、分页和筛选前统计。
- CompanyListTests.test_excludes_archived_hidden_and_foreign_companies：验证归档、分类和身份范围。
- CompanyListTests.test_invalid_latest_analysis_is_not_replaced_by_older_result：验证不可见分析不回退旧结果。
- CompanyListTests.test_score_versions_and_repeated_reads：验证版本过滤与请求间变更可见性。
- CompanyListTests.test_invalid_pagination_still_rejected：验证非法分页仍显式失败。
- CompanyListTests.test_list_does_not_load_mail_body_or_old_extraction_facts：验证必要字段投影与最新摘要保持一致。
变量索引：
- 无
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.crm import ingestion, rules, selectors
from apps.crm.models import Analysis, AnalysisInput, Company, Contact, Email, Extraction, Job, Mailbox, Score
from apps.sales.models import CompanySettings


# 功能：验证公司列表的行为和批量查询规模。
# 逻辑：使用真实 ORM 与已知合成记录，不模拟查询数；正式评分模式作为基准。
# 约束：只写独立测试库，不调用模型；通过不代表线上负载容量。
@override_settings(ANALYSIS_PROVIDER="agent", LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class CompanyListTests(TestCase):
    # 功能：验证列表不读取完整邮件正文或历史抽取事实。
    # 输入：含大正文和两份摘要的真实隔离邮件记录。
    # 输出：完整列表行与独立读取相等，payload 保持 deferred，构造行无附加查询。
    # 逻辑：从真实 ORM 投影和 SQL 查询记录验证字段裁剪，避免仅比较合成返回值。
    # 约束：正文保持数据库原值；详情和 Agent 完整上下文必须仍可读到正文。
    def test_list_does_not_load_mail_body_or_old_extraction_facts(self):
        company = self.company(1)
        email = company.emails.get()
        email.payload = {**email.payload, "body_text": "large-body-" * 10000}
        email.save(update_fields=["payload"])
        Extraction.objects.create(email=email, prompt_version="new-summary", status="completed", facts={"message_summary": "Latest summary"})
        expected = selectors.company_row(company)
        projection = selectors.list_projection([company])[company.pk]
        self.assertIn("payload", projection["emails"][0].get_deferred_fields())
        with CaptureQueriesContext(connection) as queries:
            actual = selectors.company_row(company, projection)
        self.assertEqual(len(queries), 0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["headline_summary"], "Latest summary")
        self.assertEqual(selectors.context_pair(company, include_priority=False)[1]["emails"][0]["body_text"], email.payload["body_text"])

    # 功能：创建测试归属。
    # 输入：无显式参数，读取隔离测试数据库。
    # 输出：owner、other、mailbox 实例状态。
    # 逻辑：不使用实际账号或服务令牌。
    # 约束：由 TestCase 回滚记录。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="list-owner")
        self.other = get_user_model().objects.create_user(username="list-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="owner@list.example")

    # 功能：创建一家公司及合法邮件、成功分析和正式评分。
    # 输入：`index` 决定独立域名、分数和信号；`owner` 可指定另一测试归属，默认使用 self.owner。
    # 输出：Company 对象。
    # 逻辑：邮件通过真实入库服务，分析与评分采用明确合成载荷，只验证列表投影字段。
    # 约束：不声称合成分析已通过模型评价，分数仅为测试数据。
    def company(self, index, owner=None):
        owner = owner or self.owner
        mailbox = self.mailbox if owner == self.owner else Mailbox.objects.create(owner=owner, address=f"other{index}@list.example")
        payload = rules.extract_email(mailbox, f"buyer@company{index}.example", "设备询价", "需求：设备", f"case-{index}")
        ingestion.submit_emails(owner, [payload])
        email = Email.objects.get(pk=payload["dedupe_key"])
        company = email.company
        company.name = f"Company {index}"
        company.save(update_fields=["name"])
        snapshot = AnalysisInput.objects.create(company=company, revision=company.revision, input_version="fixture",
            payload={"member_dedupe_keys": [email.pk]})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="fixture", provider="agent",
            payload={"status": "completed", "generated_at": "2026-09-26T00:00:00Z", "list_view": {
                "signal": "high" if index % 2 else "low", "industry": "test", "size_band": "small"}})
        Score.objects.create(analysis=analysis, score_version="score-v2", value=index,
            payload={"score_reasons": [{"feature": "urgency", "contribution": index}], "scored_at": "2026-09-26T00:00:00Z"})
        return company

    # 功能：验证批量与独立路径返回相同完整行。
    # 输入：真实邮件、手工主联系人、无邮件联系人、非业务联系人及更新抽取版本。
    # 输出：完整字典相等，当前摘要、主要联系人与来源一致。
    # 逻辑：对多公司同时预取后逐行与详情使用的独立 company_row 比较。
    # 约束：测试不替换 ORM 或结果生成函数。
    def test_batch_matches_independent_projection(self):
        first, second = self.company(1), self.company(2)
        email = first.emails.get()
        Extraction.objects.create(email=email, prompt_version="new-fixture", status="completed", facts={"message_summary": "Current summary"})
        manual = Contact.objects.create(company=first, email="manual@company1.example", name="Manual")
        CompanySettings.objects.create(owner=self.owner, company=first, primary_contact=manual)
        hidden_contact = Contact.objects.create(company=first, email="hidden@company1.example")
        Email.objects.create(company=first, mailbox=self.mailbox, contact=hidden_contact, dedupe_key="hidden-fixture",
            business_classification="non_business", payload={}, sent_at=timezone.now(), received_at=timezone.now(), direction="inbound")
        companies = [first, second]
        projection = selectors.list_projection(companies)
        for company in companies:
            self.assertEqual(selectors.company_row(company), selectors.company_row(company, projection[company.pk]))
        row = selectors.company_row(first, projection[first.pk])
        self.assertEqual(row["headline_summary"], "Current summary")
        self.assertEqual([c["contact_email"] for c in row["contacts"] if c["is_primary"]], [manual.email])
        self.assertNotIn(hidden_contact.email, [c["contact_email"] for c in row["contacts"]])

    # 功能：验证查询量不会按公司数线性增长。
    # 输入：先两家、再三十家各有邮件、抽取、任务、分析和评分的公司。
    # 输出：两次查询数相等，均不超过 8；三十家统计与分页数量正确。
    # 逻辑：捕获真实数据库执行，不通过 mock 伪造查询预算。
    # 约束：不含实验补充资料的样本；补充资料核验仍保留独立语义。
    def test_query_count_does_not_grow_per_company(self):
        self.company(1)
        self.company(2)
        with CaptureQueriesContext(connection) as small:
            selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        for index in range(3, 31):
            self.company(index)
        with CaptureQueriesContext(connection) as large:
            response = selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        self.assertEqual(len(small), len(large))
        self.assertLessEqual(len(large), 8)
        self.assertEqual((response["count"], len(response["results"]), response["stats"]["new_emails_today"]), (30, 20, 30))

    # 功能：验证先全局筛选排序、后分页及筛选前统计。
    # 输入：分值递增且信号交替的六家公司，一封邮件的接收日期设为昨天。
    # 输出：第二页保留正确分数、筛选总数和全部授权集合的统计。
    # 逻辑：高信号公司分值为 1、3、5，page=2 且 size=1 必须取 3。
    # 约束：不改变生产排序权重、时区或默认分页。
    def test_filter_page_and_stats_preserve_global_scope(self):
        companies = [self.company(index) for index in range(1, 7)]
        companies[0].emails.update(received_at=timezone.now() - timedelta(days=1))
        response = selectors.list_companies(Company.objects.filter(owner=self.owner), {"signal": "high", "page": "2", "page_size": "1"})
        self.assertEqual(response["count"], 3)
        self.assertEqual(response["results"][0]["score"], 3)
        self.assertEqual(response["stats"], {"companies": 6, "unregistered": 6, "new_emails_today": 5})
        self.assertEqual(selectors.list_companies(Company.objects.filter(owner=self.owner), {"q": "BUYER@COMPANY4.EXAMPLE"})["count"], 1)

    # 功能：验证批量查询不会扩展授权或可见集合。
    # 输入：正常、归档、仅非业务、无邮件及其他 owner 公司。
    # 输出：只有正常的授权公司可见，统计不包含其他记录。
    # 逻辑：真实关系过滤进入批量读取前执行；空结果仍返回零统计。
    # 约束：调用者传入 owner 范围，批量加载不得重新使用全库。
    def test_excludes_archived_hidden_and_foreign_companies(self):
        visible, archived, hidden = [self.company(index) for index in range(1, 4)]
        CompanySettings.objects.create(owner=self.owner, company=archived, archived=True)
        hidden.emails.update(business_classification="non_business")
        Company.objects.create(owner=self.owner, group_key="empty")
        self.company(9, self.other)
        result = selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        self.assertEqual([row["company_id"] for row in result["results"]], [str(visible.pk)])
        self.assertEqual(result["stats"]["companies"], 1)
        self.assertEqual(selectors.list_companies(Company.objects.none(), {})["count"], 0)

    # 功能：验证不可见的新分析不会显示旧画像。
    # 输入：一份有效旧分析与引用隐藏来源的新成功分析。
    # 输出：批量与独立路径均隐藏分析和评分，随后实验资料不匹配也隐藏。
    # 逻辑：先选择最新成功分析，再验证可见性；保持既有拒绝语义。
    # 约束：不采用回退，不删除历史记录，不调用真实模型。
    def test_invalid_latest_analysis_is_not_replaced_by_older_result(self):
        company = self.company(1)
        snapshot = AnalysisInput.objects.create(company=company, revision=company.revision, input_version="new",
            payload={"member_dedupe_keys": ["no-longer-visible"]})
        Analysis.objects.create(snapshot=snapshot, prompt_version="new", provider="agent", payload={"status": "completed"})
        projection = selectors.list_projection([company])
        self.assertEqual(selectors.latest_result(company, projection[company.pk]), (None, None))
        self.assertEqual(selectors.company_row(company), selectors.company_row(company, projection[company.pk]))
        snapshot.payload = {"member_dedupe_keys": [], "business_context": {"company_enrichment": {"status": "matched"}}}
        snapshot.save(update_fields=["payload"])
        with self.settings(WORKSPACE_OWNER_ONLY=True):
            self.assertEqual(selectors.latest_result(company, selectors.list_projection([company])[company.pk]), (None, None))

    # 功能：验证评分版本、最新失败分析及跨请求更新行为。
    # 输入：正式评分后的旧规则评分，以及其后失败分析和已完成任务。
    # 输出：agent 显示正式分，rules 显示最新分，下一请求看到最新任务状态。
    # 逻辑：请求投影不存入持久缓存；分数只属于选中的成功分析。
    # 约束：只在测试覆盖中切换 provider，不修改运行默认值。
    def test_score_versions_and_repeated_reads(self):
        company = self.company(1)
        analysis = Analysis.objects.get(snapshot__company=company)
        Score.objects.create(analysis=analysis, score_version="rules-score-v1", value=99, payload={"score_reasons": [], "scored_at": "fixture"})
        Analysis.objects.create(snapshot=analysis.snapshot, prompt_version="failed", provider="agent", payload={"status": "failed"})
        query = Company.objects.filter(owner=self.owner)
        self.assertEqual(selectors.list_companies(query, {})["results"][0]["score"], 1)
        with self.settings(ANALYSIS_PROVIDER="rules"):
            self.assertEqual(selectors.list_companies(query, {})["results"][0]["score"], 99)
        Job.objects.filter(company=company).update(status="completed")
        self.assertEqual(selectors.list_companies(query, {})["results"][0]["job_status"], "completed")

    # 功能：验证分页参数约束保留。
    # 输入：非整数、零页、负大小和超上限大小。
    # 输出：每种输入均抛 ValidationError。
    # 逻辑：保持现有整数转换和范围拒绝规则。
    # 约束：不放宽上限，不静默纠正用户输入。
    def test_invalid_pagination_still_rejected(self):
        for params in ({"page": "invalid"}, {"page": "0"}, {"page_size": "-1"}, {"page_size": "101"}):
            with self.subTest(params=params), self.assertRaises(ValidationError):
                selectors.list_companies(Company.objects.none(), params)
