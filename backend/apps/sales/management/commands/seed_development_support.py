"""Responsibility: Explicitly create fictional database placeholders for global-insight and opportunity-algorithm integration.
Implementation: Initialize each account/batch only once; commit fixed business examples, dates relative to initial import, source labels, and audit manifests together.
Relationships: The world-map page and algorithm-integration clients read ordinary database APIs; preserve Agent, algorithms, and existing real business records.
Directory:
- seed_support: Atomically create fictional data.
- Command: Initialize integration data.
- Command.add_arguments: Declare account arguments.
- Command.handle: Resolve identity and execute import.
Variable index:
- BATCH: Idempotent import batch.
- PLACES: Real city coordinates and countries for explicitly fictional events.
- Command.help: Command description.
"""

import json
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from common.laboratory import enabled
from apps.crm.models import Company
from apps.accounts.models import CompanyProfile, SalesSetup, SetupDocument
from apps.sales.models import WorldEvent, WorldNews, Opportunity, OpportunitySignal, OpportunityPriority, Product, SellerProfile, AuditEvent

BATCH = "development-support-v1"
PLACES = [("SG", "新加坡", 1.352, 103.819), ("SG", "新加坡", 1.352, 103.819), ("TW", "台北", 25.033, 121.565), ("CN", "上海", 31.23, 121.47), ("DE", "慕尼黑", 48.135, 11.582), ("JP", "东京", 35.676, 139.65), ("US", "旧金山", 37.775, -122.419), ("KR", "首尔", 37.566, 126.978)]


# Function: Import a repeatable fictional placeholder batch.
# Inputs: `actor`: explicitly selected local business identity.
# Outputs: Record ID manifest; existing batches return the original manifest.
# Logic: Lock the account, then create independent fictional companies/opportunities, 8 events, 4 news items, signals, and manually fixed scores. Add profile placeholders only when absent.
# Constraints: DEBUG or open experiment mode only; all content is explicitly fictional. No external calls, analysis, or profile overwrites; failures roll back the whole batch.
@transaction.atomic
def seed_support(actor):
    if not settings.DEBUG and not enabled():
        raise CommandError("占位导入仅允许开发或实验模式。")
    actor = get_user_model().objects.select_for_update().get(pk=actor.pk)
    previous = AuditEvent.objects.filter(owner=actor, object_id=BATCH, event="support_seed_completed").first()
    if previous:
        return previous.changes
    now = timezone.now().replace(microsecond=0)
    manifest = {"data_source": "synthetic", "base_date": now.isoformat(), "events": [], "news": [], "opportunities": [], "companies": [], "priorities": [], "signals": []}
    product = Product.objects.create(owner=actor, sku="SUPPORT-PLACEHOLDER-01", name="【虚拟】光学检测设备", description="联调占位，不构成实际产品或报价。", currency="SGD", unit_price="12000.00")
    document = SetupDocument.objects.create(owner=actor, name="虚拟销售方案.txt", content_type="text/plain; charset=utf-8", content="虚拟联调方案：提供检测设备与安装支持。所有价格和客户为占位，不可用于真实报价。".encode())
    CompanyProfile.objects.get_or_create(owner=actor, defaults={"company_name": "【虚拟】联调设备公司", "industry": "光学检测", "size_band": "50_100", "website": "https://seller.example", "description": "虚拟销售方资料，仅供接口联调。"})
    SalesSetup.objects.get_or_create(owner=actor, defaults={"completed": True, "personal": {"name": "虚拟销售代表", "title": "销售经理", "email": "sales@seller.example", "phone": "", "regions": ["新加坡"], "industries": ["光学检测"]}, "products": [{"id": str(uuid.uuid4()), "name": product.name, "category": "检测设备", "specifications": ["虚拟规格 1 mm"], "price_min": "10000.00", "price_max": "12000.00", "currency": "SGD", "scenarios": ["产线检测"], "document_id": str(document.pk), "linked_product_id": str(product.pk)}], "solutions": [{"id": str(uuid.uuid4()), "name": "【虚拟】标准销售方案", "document_id": str(document.pk)}]})
    SellerProfile.objects.get_or_create(owner=actor, defaults={"profile": {"target_industries": ["光学检测"], "target_company_size": {"min": 20, "max": 500}, "service_regions": ["Singapore"], "time_zone": "Asia/Singapore"}})
    for index, (country, city, lat, lng) in enumerate(PLACES):
        company = Company.objects.create(owner=actor, group_key=f"domain:support-{index}.example", name=f"【虚拟】{city}检测客户 {index + 1}", domains=[f"support-{index}.example"], crm_status="registered", customer={"country": country, "industry_from_crm": "光学检测", "employee_count": 80, "employee_count_source": "synthetic", "data_source": "synthetic"})
        opportunity = Opportunity.objects.create(owner=actor, company=company, title=f"【虚拟】{city}设备采购 {index + 1}", description="虚拟在手商机，仅用于页面和算法接口联调。", status="proposal", amount=str(60000 + index * 25000), currency="SGD", product_names=[product.name])
        event = WorldEvent.objects.create(owner=actor, title=f"【虚拟】{city}检测技术交流 {index + 1}", event_type="sales" if index % 3 == 1 else "exhibition", country=country, city=city, latitude=lat, longitude=lng, starts_at=now + timedelta(days=7 + index * 6), ends_at=now + timedelta(days=9 + index * 6), registration_deadline=now + timedelta(days=3 + index * 6), source_url="", description="虚拟占位：可在活动期间与关联客户讨论现有采购需求；等待算法提供正式说明。", onsite=["虚拟客户采购团队到场", "虚拟设备演示环节"], suggested_actions=["核对关联商机需求", "准备产品规格和销售方案"], opportunity_ids=[str(opportunity.pk)], data_source="synthetic")
        signal = OpportunitySignal.objects.create(owner=actor, company=company, opportunity=opportunity, signal_type="FORMAL_QUOTATION_REQUEST", signal_value="虚拟报价请求", confidence=0.9, source_type="SYNTHETIC", source_id=f"placeholder-{index + 1}", evidence_text="虚拟原文：请提供检测设备方案及报价。", data_source="synthetic")
        priority = OpportunityPriority.objects.create(owner=actor, company=company, opportunity=opportunity, priority_score=80 - index * 3, score_breakdown={"note": "固定虚拟分数，不是算法计算结果"}, top_reasons=[{"title": "虚拟客户希望了解报价", "evidence": signal.evidence_text, "source_id": signal.source_id}], evidence=[{"source_type": "SYNTHETIC", "source_id": signal.source_id, "text": signal.evidence_text}], recommended_next_action="虚拟建议：准备报价草稿并核对客户需求。", score_version="placeholder-v1", data_source="synthetic")
        for key, record in (("companies", company), ("opportunities", opportunity), ("events", event), ("signals", signal), ("priorities", priority)):
            manifest[key].append(str(record.pk))
    for index, category in enumerate(("regulation", "industry", "competition", "price")):
        news = WorldNews.objects.create(owner=actor, title=f"【虚拟】行业联调资讯 {index + 1}", category=category, industry="光学检测", country=PLACES[index][0], published_at=now - timedelta(days=index * 3), source_url="", summary="虚拟资讯占位，用于验证分类、时间筛选和详情展示。", content="这不是实际新闻。算法侧接入后可创建正式资讯或显式更新这条记录，并将 data_source 改为 agent，填写真实来源。", data_source="synthetic")
        manifest["news"].append(str(news.pk))
    manifest["product"] = str(product.pk)
    manifest["document"] = str(document.pk)
    AuditEvent.objects.create(owner=actor, actor=actor, event="support_seed_completed", object_type="development_fixture", object_id=BATCH, changes=manifest)
    return manifest


# Function: Provide explicit integration initialization.
# Logic: Create placeholders only for the selected account; default to the dedicated experiment account when unspecified.
# Constraints: Do not create usable login passwords or trigger external actions.
class Command(BaseCommand):
    help = "创建数据库虚拟占位；重复运行不覆盖已有批次。"

    # Function: Declare the owning account.
    # Inputs: `parser`: command argument parser.
    # Outputs: Outputs: None.
    # Logic: Use the dedicated algorithm-lab identity by default.
    # Constraints: Do not read or print credentials.
    def add_arguments(self, parser):
        parser.add_argument("--username", default="algorithm-lab")

    # Function: Execute explicit import.
    # Inputs: `args`, `options`: management-command inputs.
    # Outputs: JSON manifest on standard output.
    # Logic: Check mode before obtaining the account; new accounts use unusable passwords.
    # Constraints: Preserve existing account permissions and batches.
    def handle(self, *args, **options):
        if not settings.DEBUG and not enabled():
            raise CommandError("占位导入仅允许开发或实验模式。")
        actor, _ = get_user_model().objects.get_or_create(username=options["username"], defaults={"password": "!"})
        self.stdout.write(json.dumps(seed_support(actor), ensure_ascii=False))
