"""职责：生成可精确撤销的百级业务夹具，供 KG 建模和页面联调。
实现：合成来源与实际事实结构分别标注；明确批次、固定事件时间、事务写入、主键清单及内容指纹；验证和删除拒绝漂移及外部引用。
关联：复用现有 ORM、规则抽取和只读投影；common.fixture_integrity 与实验共享统一行指纹；不调用外部服务或入队 Worker，不修改既有记录。
目录：
- attachment_path：限定附件文件位于批次目录。
- FixtureBuilder：构建一个关联完整的虚构批次。
- FixtureBuilder.__init__：保存批次与归属状态。
- FixtureBuilder.add：验证并创建记录，登记创建顺序。
- FixtureBuilder.label：生成可检索显示标记。
- FixtureBuilder.accounts：创建禁用的资料占位账号。
- FixtureBuilder.business：创建客户及关系业务。
- FixtureBuilder.mail_and_analysis：生成合成邮件、历史及当前分析快照。
- FixtureBuilder.knowledge_and_chat：生成知识、外部资料和显式模拟聊天记录。
- FixtureBuilder.build：串行组装全部记录并保存附件。
- load_manifest：读取唯一批次清单。
- verify_manifest：校验清单完整性、行指纹及附件。
- external_references：检查清单之外的外键引用。
- run_seed：事务生成或验证重放批次。
- deletion_order：根据当前外键关系生成子记录优先的删除序列。
- run_delete：先核验再按当前依赖删除精确清单中的记录。
- Command：管理命令入口。
- Command.add_arguments：声明显式参数与写入确认开关。
- Command.handle：校验环境并路由生成、验证或删除预览。
变量索引：
- BASE_TIME：本批次模拟事件基准时间，非模型运行时间。
- EVENT：数据库内清单的唯一审计事件类型。
- INDUSTRIES：循环分配的演示行业。
- EXCLUDED：不灌入夹具的凭证、调度和账号控制模型。
- logger：仅输出批次、阶段、表名和数量的操作日志。
"""

import hashlib
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.crm import rules
from apps.crm.selectors import context_pair
from apps.sales.models import AuditEvent
from common.fixture_integrity import fingerprint

BASE_TIME = datetime(2026, 9, 21, tzinfo=timezone.utc)
EVENT = "kg_synthetic_batch_v1"
INDUSTRIES = ("半导体检测", "精密量测", "光学检测", "工业检测")
EXCLUDED = {
    "accounts.AccountReset": "账户重置协调状态",
    "crm.GmailCredential": "真实邮箱授权", "crm.AgentCredential": "服务认证",
    "crm.QQCredential": "真实邮箱授权", "sales.Connection": "外部写权限连接",
    "agent_tools.ToolCredential": "工具认证授权",
    "crm.Job": "模型任务队列", "crm.MailboxSyncRun": "邮箱同步队列",
    "crm.EmailProcessingJob": "邮件处理队列", "crm.ExtractionRepair": "修复任务队列",
    "crm.SyncCheckpoint": "实际同步游标", "crm.QQSyncCheckpoint": "实际同步游标",
}
logger = logging.getLogger("salesmate.kg_seed")


# 功能：限定夹具附件的访问边界。
# 输入：`actor_id` 归属用户 ID、`batch` 合法批次、`key` 相对存储键。
# 输出：经过解析的绝对 Path；越界抛 CommandError。
# 逻辑：仅允许当前账号当前批次目录内的单个文件，删除不使用递归或正则目录匹配。
# 约束：调用方须先校验 batch；不接受现有普通业务附件路径。
def attachment_path(actor_id, batch, key):
    root = (settings.BASE_DIR / "private_uploads").resolve()
    scope = (root / str(actor_id) / batch).resolve()
    path = (root / key).resolve()
    if not scope.is_relative_to(root) or path.parent != scope:
        raise CommandError("附件路径不属于本次夹具批次。")
    return path


# 功能：生成明确标注为人工合成的跨表业务场景。
# 逻辑：每个场景一个客户及配套业务；历史分析独立失效，当前分析仍可展示。
# 约束：add 直接写夹具，不调用会入队或发送的业务写服务；不存在真实交易或模型执行。
class FixtureBuilder:
    # 功能：保存生成上下文。
    # 输入：`actor` 既有员工、`batch` 批次字符串、`count` 场景数。
    # 输出：新实例；记录顺序、待写附件和标签真值保存在实例状态。
    # 逻辑：可见名称使用统一前缀；time 为固定模拟事件时间。
    # 约束：actor 不会被改写；调用方管理数据库事务。
    def __init__(self, actor, batch, count):
        self.actor, self.batch, self.count = actor, batch, count
        self.rows, self.files, self.truth = [], [], []
        self.sequence = Counter()
        self.time = BASE_TIME

    # 功能：创建并登记单条夹具。
    # 输入：`model_label` 模型名及 `fields` 字段参数；读取批次和模型内序号。
    # 输出：持久化模型实例。
    # 逻辑：UUID 使用批次与模型序号确定生成，执行字段、唯一性和数据库约束校验。
    # 约束：空的默认 JSON 和可空字段按模型持久化契约跳过表单必填检查；不覆盖旧行。
    def add(self, model_label, **fields):
        model = apps.get_model(model_label)
        self.sequence[model_label] += 1
        if model._meta.pk.get_internal_type() == "UUIDField" and model._meta.pk.name not in fields:
            fields[model._meta.pk.name] = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch}:{model_label}:{self.sequence[model_label]}")
        obj = model(**fields)
        exclusions = [f.name for f in obj._meta.concrete_fields if f.null and getattr(obj, f.attname) is None
                      or f.has_default() and getattr(obj, f.attname) in ({}, [])]
        obj.full_clean(exclude=exclusions)
        obj.save(force_insert=True)
        self.rows.append({"model": model_label, "pk": str(obj.pk)})
        return obj

    # 功能：生成批次可见标记。
    # 输入：`text` 业务描述、`index` 场景序号。
    # 输出：包含批次及虚构提示的字符串。
    # 逻辑：文本前缀固定，便于正则发现批次。
    # 约束：删除仍使用清单主键，不按任意正文命中批量删除。
    def label(self, text, index):
        return f"[{self.batch}][虚构] {text} {index:03d}"

    # 功能：为一对一资料表生成独立禁用账号。
    # 输入：`index` 场景号、`industry` 行业；读取实例的员工和批次状态。
    # 输出：禁用用户；创建其资料、引导文档、引导配置及销售目标资料。
    # 逻辑：密码不可用且账号禁用，无组权限，无凭证，不修改真实员工资料。
    # 约束：这些账号仅满足一对一约束，不用于登录或后台领取任务。
    def accounts(self, index, industry):
        user = self.add("accounts.User", username=f"{self.batch.lower().replace('_', '-')}_{index:03d}",
                        password="!synthetic-unusable", is_active=False,
                        first_name=self.label("员工", index), email=f"user-{index}@{self.batch.lower().replace('_', '-')}.example")
        self.add("accounts.CompanyProfile", owner=user, company_name=self.label("销售企业", index),
                 industry=industry, size_band="50_100", website=f"https://seller-{index}.example",
                 description=self.label("仅供实验的企业资料", index))
        document = self.add("accounts.SetupDocument", owner=user, name=self.label("规格说明.txt", index),
                            content_type="text/plain", content=self.label("虚构设备规格，非真实文件。", index).encode())
        self.add("accounts.SalesSetup", owner=user, personal={"name": user.first_name, "title": "销售工程师",
                 "email": user.email, "phone": "", "regions": ["SG"], "industries": [industry]},
                 products=[], solutions=[{"id": str(document.pk), "name": document.name,
                 "document_id": str(document.pk)}], completed=True)
        self.add("sales.SellerProfile", owner=user, profile={"synthetic_batch": self.batch,
                 "target_industries": [industry], "target_regions": ["SG"], "timezone": "Asia/Singapore"})
        return user

    # 功能：建立客户、产品、交易草稿及协作关系。
    # 输入：`index` 场景号、`user` 禁用资料账号、`industry` 行业。
    # 输出：场景字典，包含后续邮件与知识所需 ORM 对象。
    # 逻辑：报价和订单均为草稿，跟进为取消状态，外部动作明确取消；团队仅包含禁用账号。
    # 约束：不创建可执行动作、真实成交或外部连接；不会同步或修改其他客户的优先级。
    def business(self, index, user, industry):
        owner = self.actor
        domain = f"c{index:03d}.{self.batch.lower().replace('_', '-')}.example"
        company = self.add("crm.Company", owner=owner, group_key=f"domain:{domain}", name=self.label("客户", index),
                           domains=[domain], crm_status="registered", revision=1,
                           customer={"synthetic_batch": self.batch, "industry_from_crm": industry,
                                     "employee_count": 50 + index, "employee_count_source": "synthetic_sample"})
        contact = self.add("crm.Contact", company=company, email=f"buyer@{domain}", name=self.label("采购联系人", index))
        self.add("sales.CompanySettings", owner=owner, company=company, primary_contact=contact,
                 notes=self.label("仅供知识图谱关联练习", index))
        self.add("sales.CompanyAlias", owner=owner, company=company, group_key=f"domain:alias.{domain}")
        self.add("sales.ContactProfile", owner=owner, contact=contact, title="采购经理", notes=self.label("联系人补充", index))
        team = self.add("sales.Team", owner=owner, name=self.label("实验团队", index))
        self.add("sales.Membership", owner=owner, team=team, user=user, role="viewer")
        self.add("sales.CompanyGrant", owner=owner, company=company, team=team, role="viewer")
        product = self.add("sales.Product", owner=owner, sku=f"{self.batch}-P{index:03d}", name=self.label("检测设备", index),
                           description=self.label(f"适用于{industry}的演示产品，参数非真实承诺", index),
                           currency="CNY", unit_price=Decimal(1000 + index * 100), stock_quantity=Decimal(index % 30))
        ticket = self.add("sales.Ticket", owner=owner, company=company, title=self.label("样品检测咨询", index),
                          description=self.label("询问检测精度及验收流程", index), status=("open", "in_progress", "resolved", "closed")[index % 4],
                          priority=("low", "normal", "high")[index % 3])
        opportunity = self.add("sales.Opportunity", owner=owner, company=company, title=self.label("产线设备评估", index),
                              description=self.label("虚构采购线索，不代表已成交", index), status=("new", "qualified", "proposal")[index % 3],
                              currency="CNY", amount=None if index % 5 == 0 else product.unit_price * 3,
                              product_names=[product.name], expected_close=(self.time + timedelta(days=30)).date())
        quote = self.add("sales.Quote", owner=owner, company=company, number=f"{self.batch}-Q{index:03d}",
                         currency="CNY", notes=self.label("虚构报价草稿，未发送", index), valid_until=(self.time + timedelta(days=30)).date())
        self.add("sales.QuoteLine", owner=owner, quote=quote, product=product, description=product.name,
                 quantity=Decimal(3), unit_price=product.unit_price)
        order = self.add("sales.SalesOrder", owner=owner, company=company, quote=quote, number=f"{self.batch}-O{index:03d}",
                         currency="CNY", notes=self.label("虚构订单草稿，未确认", index))
        self.add("sales.OrderLine", owner=owner, order=order, product=product, description=product.name,
                 quantity=Decimal(3), unit_price=product.unit_price)
        follow = self.add("sales.FollowUp", owner=owner, company=company, title=self.label("跟进计划", index),
                          description=self.label("取消状态，防止产生待办任务", index), due_at=self.time, status="cancelled")
        self.add("sales.Notification", owner=owner, follow_up=follow, source_revision=0,
                 title=self.label("历史提醒样例", index), read_at=self.time)
        conversation = self.add("sales.Conversation", owner=owner, company=company, title=self.label("证据关联练习", index))
        self.add("sales.Draft", owner=owner, conversation=conversation, kind="email", subject=self.label("询问项目进度", index),
                 content=self.label("这是一份虚构草稿，不应发送。", index), recipients=[contact.email])
        self.add("sales.ToolAction", owner=owner, company=company, conversation=conversation, tool="gmail.send", status="cancelled",
                 idempotency_key=uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch}:action:{index}"),
                 parameters={"synthetic_batch": self.batch, "to": [contact.email], "subject": self.label("取消的发信示例", index),
                             "body": "纯虚构，未执行。"}, finished_at=self.time,
                 result={"synthetic_batch": self.batch, "executed": False})
        self.add("agent_tools.ToolCall", owner=owner, key=uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch}:call:{index}"),
                 tool="synthetic.fixture", input_hash=hashlib.sha256(f"{self.batch}:{index}".encode()).hexdigest(),
                 result={"synthetic_batch": self.batch, "executed": False, "company_id": str(company.pk)})
        self.add("agent_tools.ToolProposal", owner=owner, tool="gmail.send", status="cancelled",
                 arguments={"synthetic_batch": self.batch, "executed": False}, result={"synthetic_batch": self.batch}, expires_at=self.time)
        self.add("sales.AuditEvent", owner=owner, actor=owner, company=company, event="synthetic_fixture_created",
                 object_type="crm.Company", object_id=str(company.pk), changes={"synthetic_batch": self.batch})
        company.tickets = [{"ticket_id": str(ticket.pk), "title": ticket.title, "description": ticket.description,
                            "status": ticket.status, "source": "synthetic_sample"}]
        company.save(update_fields=["tickets"])
        return {"company": company, "contact": contact, "product": product, "opportunity": opportunity,
                "conversation": conversation, "industry": industry, "domain": domain}

    # 功能：创建三封合成邮件及两代可追溯分析。
    # 输入：`index` 场景号、`scene` 客户业务对象字典。
    # 输出：扩展 scene，保存 mailbox、emails、snapshots。
    # 逻辑：使用现有规则提取和模板，分别保存实际事实结构版本和合成来源；旧快照失效，新快照保留。
    # 约束：无 LLM 调用，评分值为未知且版本隔离；不把模拟关系当作真实 Agent 执行记录。
    def mail_and_analysis(self, index, scene):
        company, contact = scene["company"], scene["contact"]
        mailbox = self.add("crm.Mailbox", owner=self.actor, address=f"sales-{index}@{self.batch.lower().replace('_', '-')}.example",
                           sync_state={"synthetic_batch": self.batch, "authorized": False})
        emails = []
        for step in range(3):
            budget = "预算：尚未确定" if index % 5 == 0 else f"预算：{(index + 10) * 1000 + step * 100} 元"
            body = (f"[{self.batch}][虚构资料]\n联系人：{contact.name}\n公司：{company.name}\n行业：{scene['industry']}\n"
                    f"需求：{scene['product'].name}\n数量：3 台\n{budget}\n交期：项目立项后 30 天\n"
                    "决策流程：技术验证后提交采购审批\n顾虑：需要确认检测精度及售后响应\n尚未下单。")
            payload = rules.extract_email(mailbox, contact.email, self.label(f"询价往来第{step + 1}封", index), body,
                                          message_id=f"{self.batch}-{index}-{step}",
                                          sent_at=(self.time - timedelta(days=12 - step * 3, minutes=index)).isoformat())
            payload["synthetic_batch"] = self.batch
            payload["extract_schema_version"] = payload["extract_prompt_version"]
            payload["extract_prompt_version"] = f"{self.batch}:fixture-extract-v1"
            email = self.add("crm.Email", mailbox=mailbox, company=company, contact=contact, payload=payload,
                             dedupe_key=payload["dedupe_key"], sent_at=payload["sent_at"], received_at=payload["received_at"],
                             direction="inbound", classification_reason=f"{self.batch}:人工合成业务邮件")
            extraction = self.add("crm.Extraction", email=email, prompt_version=payload["extract_prompt_version"],
                                   status="completed", facts=payload["facts"])
            self.add("crm.StoredMessage", mailbox=mailbox, message_id=payload["gmail_message_id"], raw=payload,
                     submission=payload, status="completed", prompt_version=payload["extract_prompt_version"])
            emails.append((email, extraction))
        grouping, context = context_pair(company, include_priority=False)
        snapshots = []
        for revision in (0, 1):
            snapshot_data = rules.build_input(grouping, context)
            snapshot_data["synthetic_batch"] = self.batch
            snapshot = self.add("crm.AnalysisInput", company=company, input_version=snapshot_data["input_version"],
                                revision=revision, payload=snapshot_data)
            for email, extraction in emails:
                self.add("crm.SnapshotSource", snapshot=snapshot, email=email, extraction=extraction, review_revision=0)
            analysis_data = rules.generate_analysis(snapshot_data, grouping, context)
            analysis_data["synthetic_batch"] = self.batch
            analysis_data["analysis_prompt_version"] = f"{self.batch}:fixture-analysis-v1"
            analysis = self.add("crm.Analysis", snapshot=snapshot, prompt_version=analysis_data["analysis_prompt_version"],
                                payload=analysis_data, provider="synthetic")
            self.add("crm.Score", analysis=analysis, score_version=f"{self.batch}:not-scored", value=None,
                     payload={"synthetic_batch": self.batch, "score": None, "score_reasons": [], "scored_at": self.time.isoformat()})
            if revision == 0:
                self.add("crm.SnapshotInvalidation", snapshot=snapshot, reason=f"{self.batch}:synthetic_revision")
            snapshots.append(snapshot)
        scene.update(mailbox=mailbox, emails=emails, snapshots=snapshots)

    # 功能：创建公告样例、内部知识与显式模拟的引用关系。
    # 输入：`index` 场景号、`scene` 已有客户及邮件。
    # 输出：无；登记知识、公告样例、聊天、向量和附件元数据。
    # 逻辑：5 种事件场景循环；标注真值独立放入批次清单，未泄露到资讯正文。
    # 约束：新闻和运行记录均为虚构；合成向量使用独立空间，不能用于评估真实语义检索。
    def knowledge_and_chat(self, index, scene):
        owner, company = self.actor, scene["company"]
        kinds = ("扩产计划", "项目延期", "预算未定", "同名企业", "旧闻更新")
        kind = kinds[(index - 1) % len(kinds)]
        text = self.label(f"{company.name}的{kind}资料；仅作演示，不代表真实公告。", index)
        external = self.add("chat.KnowledgeEntry", owner=owner, source_key=f"{self.batch}:external-announcement:{index}",
                            version="synthetic-v1", title=self.label(kind + "公告样例", index),
                            content=text + f"\n虚构来源：https://{'other-' if kind == '同名企业' else ''}{scene['domain']}/news/{index}"
                            + "\n项目状态需向客户确认；本文未披露已下单信息。")
        knowledge = self.add("chat.KnowledgeEntry", owner=owner, source_key=f"{self.batch}:knowledge:{index}", version="synthetic-v1",
                             title=self.label("设备与方案说明", index), content=scene["product"].description)
        source = {"source_id": f"knowledge:{knowledge.pk}", "source_type": "internal_knowledge",
                  "title_or_label": knowledge.title, "content": knowledge.content}
        user_message = self.add("sales.Message", owner=owner, conversation=scene["conversation"], role="user",
                                content=self.label("请核对客户需求和资料依据。", index),
                                client_key=uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch}:user:{index}"))
        answer = self.add("sales.Message", owner=owner, conversation=scene["conversation"], role="assistant",
                          content=self.label("人工构造的聊天样例，未调用模型。产品说明可见引用[1]；采购状态仍待确认。", index),
                          client_key=uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch}:assistant:{index}"))
        request = self.add("chat.AnswerRequest", owner=owner, company=company, conversation=scene["conversation"],
                           user_message=user_message, assistant_message=answer, status="completed",
                           processing_started_at=self.time, finished_at=self.time,
                           context_snapshot={"synthetic_batch": self.batch, "customer_context": [], "context_items": [source]},
                           result={"synthetic_batch": self.batch, "executed": False, "assistant_text": answer.content},
                           chat_prompt_version=f"{self.batch}:fixture-chat-v1")
        self.add("chat.Citation", request=request, position=1, source_id=source["source_id"], source_type=source["source_type"],
                 title_or_label=source["title_or_label"], content=source["content"])
        self.add("chat.ToolRead", request=request, tool="synthetic.customers.context",
                 arguments={"synthetic_batch": self.batch, "company_id": str(company.pk)},
                 result={"synthetic_batch": self.batch, "executed": False}, evidence_items=[source])
        self.add("vectors.VectorDocument", owner=owner, namespace=self.batch, source=f"{self.batch}:knowledge:{index}",
                 model="synthetic-nonsemantic-v1", dimensions=3, content=knowledge.content,
                 content_hash=hashlib.sha256(knowledge.content.encode()).hexdigest(), embedding=[1.0, float(index), float(index % 7)])
        contents = (text + "\n此文件为虚构数据库附件，不包含真实业务信息。\n").encode()
        key = f"{owner.pk}/{self.batch}/{index:03d}.txt"
        digest = hashlib.sha256(contents).hexdigest()
        self.add("sales.Attachment", owner=owner, company=company, name=self.label("资料.txt", index),
                 storage_key=key, content_type="text/plain", size=len(contents), sha256=digest)
        self.files.append({"key": key, "sha256": digest, "content": contents})
        self.truth.append({"company_id": str(company.pk), "external_knowledge_id": str(external.pk), "scenario": kind,
                           "expected_company_match": kind != "同名企业", "purchase_confirmed": False})

    # 功能：组装整个批次。
    # 输入：无外部参数；读取实例的员工、批次和数量状态。
    # 输出：含精确行清单、指纹、附件和独立标注的字典。
    # 逻辑：先创建关联数据，再从数据库重读计算指纹；附件使用排他创建。
    # 约束：异常交由 run_seed 回滚；每个附件创建后立即登记实际路径用于失败清理。
    def build(self):
        for index in range(1, self.count + 1):
            industry = INDUSTRIES[(index - 1) % len(INDUSTRIES)]
            user = self.accounts(index, industry)
            scene = self.business(index, user, industry)
            self.mail_and_analysis(index, scene)
            self.knowledge_and_chat(index, scene)
            if index % 10 == 0 or index == self.count:
                logger.info("kg_seed_progress batch=%s scenes=%s/%s", self.batch, index, self.count)
        grouped = defaultdict(list)
        for row in self.rows:
            grouped[row["model"]].append(row["pk"])
        hashes = {}
        for label, keys in grouped.items():
            for obj in apps.get_model(label).objects.filter(pk__in=keys):
                hashes[(label, str(obj.pk))] = fingerprint(obj)
        for row in self.rows:
            row["fingerprint"] = hashes[(row["model"], row["pk"])]
        for item in self.files:
            path = attachment_path(self.actor.pk, self.batch, item["key"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                item["created"] = True
                output.write(item["content"])
        return {"batch": self.batch, "owner_id": self.actor.pk, "username": self.actor.username, "count": self.count,
                "base_time": self.time.isoformat(), "generator": "kg-business-fixture-v1", "source": "synthetic_sample",
                "rows": self.rows, "table_counts": dict(Counter(row["model"] for row in self.rows)),
                "files": [{"key": x["key"], "sha256": x["sha256"]} for x in self.files],
                "truth": self.truth, "excluded_models": EXCLUDED}


# 功能：定位数据库内的唯一批次清单。
# 输入：`actor` 员工、`batch` 完整批次。
# 输出：首条匹配的 AuditEvent 或 None；正常导入由员工锁避免重复。
# 逻辑：使用精确事件与 object_id，不按正文模糊匹配。
# 约束：不同账号使用同一批次被 Command 的身份检查拒绝。
def load_manifest(actor, batch):
    return AuditEvent.objects.filter(owner=actor, event=EVENT, object_id=batch).first()


# 功能：核对已导入的夹具是否完整且未被修改。
# 输入：`manifest` 批次清单。
# 输出：按模型汇总数量；缺失、漂移、文件损坏均抛 CommandError。
# 逻辑：分模型重读所有主键，比较内容指纹与文件摘要。
# 约束：不修改数据，也不接受部分缺失为成功；附件清理期间不使用本函数。
def verify_manifest(manifest):
    grouped = defaultdict(list)
    for row in manifest["rows"]:
        grouped[row["model"]].append(row)
    for label, rows in grouped.items():
        actual = {str(obj.pk): fingerprint(obj) for obj in apps.get_model(label).objects.filter(pk__in=[r["pk"] for r in rows])}
        for row in rows:
            if actual.get(row["pk"]) != row["fingerprint"]:
                raise CommandError(f"夹具缺失或被修改：{label} {row['pk']}，停止操作。")
    for item in manifest["files"]:
        path = attachment_path(manifest["owner_id"], manifest["batch"], item["key"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise CommandError(f"夹具附件缺失或被修改：{item['key']}")
    return {label: len(rows) for label, rows in grouped.items()}


# 功能：检测非夹具记录对夹具的引用。
# 输入：`manifest` 批次清单。
# 输出：外部引用说明列表。
# 逻辑：遍历当前 ORM 的实际外键，包括自动多对多表，排除同一批次行。
# 约束：无法自动识别任意 JSON 中的业务引用，删除说明要求在人工扩展业务前保留批次隔离。
def external_references(manifest):
    keys = defaultdict(list)
    for row in manifest["rows"]:
        keys[apps.get_model(row["model"])].append(row["pk"])
    blockers = []
    for model in apps.get_models(include_auto_created=True):
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model in keys:
                query = model.objects.filter(**{f"{field.attname}__in": keys[field.related_model]})
                if model in keys:
                    query = query.exclude(pk__in=keys[model])
                count = query.count()
                if count:
                    blockers.append(f"{model._meta.label}.{field.name}: {count}")
    return blockers


# 功能：原子导入批次或验证幂等重放。
# 输入：`actor` 已授权目标账号、`batch` 批次、`count` 场景数。
# 输出：数据库内清单；首次导入失败时全部业务行回滚。
# 逻辑：锁定账号；以审计事件保存精确主键，附件异常时仅删除本次实际创建文件。
# 约束：既有同批次参数不同直接失败；不自动重试；提交后的报告写入失败不触发业务回滚。
def run_seed(actor, batch, count):
    builder = FixtureBuilder(actor, batch, count)
    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=actor.pk)
            previous = load_manifest(actor, batch)
            if previous:
                if previous.changes.get("count") != count:
                    raise CommandError("已有批次数量不同，不能覆盖。")
                verify_manifest(previous.changes)
                return previous.changes
            manifest = builder.build()
            AuditEvent.objects.create(owner=actor, actor=actor, event=EVENT, object_type="synthetic_batch",
                                      object_id=batch, changes=manifest)
            verify_manifest(manifest)
            return manifest
    except Exception:
        for item in builder.files:
            if item.get("created"):
                attachment_path(actor.pk, batch, item["key"]).unlink(missing_ok=True)
        logger.exception("kg_seed_failed batch=%s; database transaction rolled back", batch)
        raise


# 功能：按当前关系计算清单的删除顺序。
# 输入：`manifest` 已校验的批次清单。
# 输出：子记录优先的模型与主键序列。
# 逻辑：读取实际外键构建依赖图，拓扑排序避免编辑关系后原创建顺序失效。
# 约束：循环引用明确报错，不隐式断开关系；调用方在同一事务持有清单与行锁。
def deletion_order(manifest):
    rows = {(row["model"], row["pk"]): row for row in manifest["rows"]}
    dependencies = {key: set() for key in rows}
    for key in rows:
        record = apps.get_model(key[0]).objects.get(pk=key[1])
        for field in record._meta.concrete_fields:
            if field.is_relation:
                parent = (field.related_model._meta.label, str(getattr(record, field.attname)))
                if parent in rows and parent != key:
                    dependencies[parent].add(key)
    result = []
    while dependencies:
        leaves = {key for key, children in dependencies.items() if not children}
        if not leaves:
            raise CommandError("批次存在循环引用，请先调整关系再清理；未删除任何记录。")
        for key in sorted(leaves):
            result.append(rows[key])
            del dependencies[key]
        for children in dependencies.values():
            children.difference_update(leaves)
    return result


# 功能：删除预览或执行精确批次清理。
# 输入：`actor` 员工、`batch` 批次、`apply` 是否明确执行。
# 输出：删除数量或预览说明。
# 逻辑：锁定员工、清单及夹具行，校验指纹并拒绝外部引用，按当前依赖删除；提交后清理精确附件。
# 约束：数据库提交后保留清理状态和文件清单，文件失败可显式重跑；不自动重试、不删除其他批次。
def run_delete(actor, batch, apply):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        entry = load_manifest(actor, batch)
        if not entry:
            raise CommandError("没有找到该账号的批次清单。")
        # 与共享维护使用同一清单行锁，等待后必须重新读取 JSON，避免清理使用旧指纹。
        entry = AuditEvent.objects.select_for_update().get(pk=entry.pk)
        manifest = entry.changes
        if manifest.get("cleanup_state") != "files_pending":
            for row in manifest["rows"]:
                list(apps.get_model(row["model"]).objects.select_for_update().filter(pk=row["pk"]))
            counts = verify_manifest(manifest)
            blockers = external_references(manifest)
            if blockers:
                raise CommandError("夹具被其他记录引用，拒绝删除：" + "; ".join(blockers))
            ordered = deletion_order(manifest)
            if not apply:
                return {"action": "delete_preview", "batch": batch, "table_counts": counts, "files": len(manifest["files"])}
            for row in ordered:
                apps.get_model(row["model"]).objects.filter(pk=row["pk"]).delete()
            manifest["cleanup_state"] = "files_pending"
            entry.changes = manifest
            entry.save(update_fields=["changes"])
        elif not apply:
            return {"action": "file_cleanup_preview", "batch": batch, "files": len(manifest["files"])}
    for item in manifest["files"]:
        attachment_path(actor.pk, batch, item["key"]).unlink(missing_ok=True)
    entry.delete()
    return {"action": "deleted", "batch": batch, "rows": len(manifest["rows"]), "files": len(manifest["files"])}


# 功能：提供夹具创建、核验和后续清理入口。
# 逻辑：绑定明确数据库名；写入必须指定 --apply，非隔离测试库另需 --allow-live-database。
# 约束：不切换 .env、不创建权限凭证、不应用迁移；数据库必须已具备当前目标表。
class Command(BaseCommand):
    # 功能：定义命令参数。
    # 输入：`parser` Django 参数解析器。
    # 输出：无，注册账号、批次、数量、操作及报告路径。
    # 逻辑：默认 seed 预览，100 场景；完整批次必须显式提供。
    # 约束：--apply 是本工具的防误操作开关，不替代操作者授权。
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--batch", required=True)
        parser.add_argument("--count", type=int, default=100)
        parser.add_argument("--action", choices=("seed", "verify", "delete"), default="seed")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database-name", required=True)
        parser.add_argument("--allow-live-database", action="store_true")
        parser.add_argument("--report")

    # 功能：校验命令上下文并执行单项操作。
    # 输入：`args` 未使用的位置参数、`options` 已解析参数及本地设置。
    # 输出：标准输出摘要；可写 JSON 报告，失败以非零状态退出。
    # 逻辑：校验批次、账号和数据库；拒绝跨账号复用标记；明确区分预览和写入。
    # 约束：只接受明确命名的 PostgreSQL；线上写入需显式开关，报告失败不隐式重复导入。
    def handle(self, *args, **options):
        batch = options["batch"]
        if not re.fullmatch(r"KGSEED_[0-9]{8}_[A-Z0-9]{2,12}", batch):
            raise CommandError("批次格式须为 KGSEED_YYYYMMDD_编号，例如 KGSEED_20260921_01。")
        if not 1 <= options["count"] <= 500:
            raise CommandError("count 须为 1–500。")
        database = connection.settings_dict["NAME"]
        if connection.vendor != "postgresql" or database != options["database_name"]:
            raise CommandError("实际数据库与显式指定的 PostgreSQL 数据库名不一致。")
        isolated = database.startswith(("test_", "salesmate_kg_restore_"))
        if options["apply"] and not isolated and not options["allow_live_database"]:
            raise CommandError("真实数据库写入必须显式指定 --allow-live-database；先完成备份。")
        actor = get_user_model().objects.get(username=options["username"], is_active=True, is_staff=False, is_superuser=False)
        if AuditEvent.objects.filter(event=EVENT, object_id=batch).exclude(owner=actor).exists():
            raise CommandError("批次已经属于其他账号。")
        if options["action"] == "delete":
            result = run_delete(actor, batch, options["apply"])
        elif options["action"] == "verify":
            entry = load_manifest(actor, batch)
            if not entry:
                raise CommandError("批次尚未导入。")
            result = {"action": "verified", "batch": batch, "table_counts": verify_manifest(entry.changes)}
        elif options["apply"]:
            result = run_seed(actor, batch, options["count"])
        else:
            result = {"action": "seed_preview", "batch": batch, "username": actor.username,
                      "count": options["count"], "excluded_models": EXCLUDED}
        if options["report"]:
            Path(options["report"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {key: value for key, value in result.items() if key not in ("rows", "truth", "files", "excluded_models")}
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
