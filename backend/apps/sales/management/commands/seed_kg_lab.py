"""Responsibility: Generate precisely reversible business fixtures at hundred-record scale for KG modeling and page integration.
Implementation: Label synthetic provenance separately from actual fact structures. Use explicit batches, fixed event times, transactional writes, primary-key manifests, and content fingerprints; verification/deletion rejects drift and external references.
Relationships: Reuse existing ORM, rule extraction, and read-only projections; common.fixture_integrity shares row fingerprints with experiment sharing. No external services, worker enqueueing, or existing-record mutations.
Directory:
- attachment_path: Restrict attachment files to the batch directory.
- FixtureBuilder: Build a fictional batch with complete relationships.
- FixtureBuilder.__init__: Store batch and ownership state.
- FixtureBuilder.add: Validate/create records and register creation order.
- FixtureBuilder.label: Generate searchable display markers.
- FixtureBuilder.accounts: Create disabled profile placeholder accounts.
- FixtureBuilder.business: Create companies and related business records.
- FixtureBuilder.mail_and_analysis: Generate synthetic emails and historical/current analysis snapshots.
- FixtureBuilder.knowledge_and_chat: Generate knowledge, external profiles, and explicitly simulated chats.
- FixtureBuilder.build: Assemble all records serially and save attachments.
- load_manifest: Read the unique batch manifest.
- verify_manifest: Validate manifest integrity, row fingerprints, and attachments.
- external_references: Check foreign-key references outside the manifest.
- run_seed: Generate a batch transactionally or verify replay.
- deletion_order: Build a child-first deletion sequence from current foreign-key relations.
- run_delete: Verify first, then delete exact manifest records in current dependency order.
- Command: Management-command entry point.
- Command.add_arguments: Declare explicit parameters and the write-confirmation switch.
- Command.handle: Validate the environment and route generation, verification, or deletion previews.
Variable index:
- BASE_TIME: Baseline simulated event time for this batch, not model execution time.
- EVENT: Unique audit event type for database manifests.
- INDUSTRIES: Demonstration industries assigned cyclically.
- EXCLUDED: Credential, scheduling, and account-control models excluded from fixtures.
- logger: Operation logs limited to batch, stage, table, and count.
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


# Function: Restrict access to fixture attachments.
# Inputs: `actor_id`: owner user ID; `batch`: valid batch; `key`: relative storage key.
# Outputs: Resolved absolute Path; path escape raises CommandError.
# Logic: Allow only one file within the current account/batch directory; deletion uses neither recursion nor regex directory matching.
# Constraints: The caller must validate batch first; existing ordinary business attachment paths are not accepted.
def attachment_path(actor_id, batch, key):
    root = (settings.BASE_DIR / "private_uploads").resolve()
    scope = (root / str(actor_id) / batch).resolve()
    path = (root / key).resolve()
    if not scope.is_relative_to(root) or path.parent != scope:
        raise CommandError("附件路径不属于本次夹具批次。")
    return path


# Function: Generate cross-table business scenarios explicitly labeled as manually synthetic.
# Logic: Each scenario has one company and related business records; historical analyses are independently invalidated while current analyses remain displayable.
# Constraints: add writes fixtures directly without business write services that enqueue or send; no real transactions or model execution occur.
class FixtureBuilder:
    # Function: Store generation context.
    # Inputs: `actor`: existing employee; `batch`: batch string; `count`: scenario count.
    # Outputs: New instance; record order, pending attachments, and label truth reside in instance state.
    # Logic: Visible names use a common prefix; time is the fixed simulated event time.
    # Constraints: Do not mutate actor; the caller manages the database transaction.
    def __init__(self, actor, batch, count):
        self.actor, self.batch, self.count = actor, batch, count
        self.rows, self.files, self.truth = [], [], []
        self.sequence = Counter()
        self.time = BASE_TIME

    # Function: Create and register one fixture.
    # Inputs: `model_label`: model name; `fields`: field arguments; read batch and per-model sequence state.
    # Outputs: Persisted model instance.
    # Logic: Derive UUIDs deterministically from batch and model sequence; validate fields, uniqueness, and database constraints.
    # Constraints: Empty default JSON and nullable fields skip form-required checks according to model persistence contracts; never overwrite existing rows.
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

    # Function: Generate a visible batch marker.
    # Inputs: `text`: business description; `index`: scenario number.
    # Outputs: String containing batch and fiction markers.
    # Logic: Use a fixed text prefix so batches are discoverable by regex.
    # Constraints: Deletion still uses manifest primary keys, never arbitrary body matches for bulk deletion.
    def label(self, text, index):
        return f"[{self.batch}][虚构] {text} {index:03d}"

    # Function: Generate an independent disabled account for one-to-one profile tables.
    # Inputs: `index`: scenario number; `industry`: industry; read employee and batch instance state.
    # Outputs: Disabled user, with profile, onboarding document/configuration, and sales-goal profile created.
    # Logic: Use an unusable password and disabled account with no group permissions or credentials; preserve real employee profiles.
    # Constraints: These accounts satisfy one-to-one constraints only, never login or background task claiming.
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

    # Function: Create companies, products, transaction drafts, and collaboration relations.
    # Inputs: `index`: scenario number; `user`: disabled profile account; `industry`: industry.
    # Outputs: Scenario dictionary with ORM objects needed for subsequent emails and knowledge.
    # Logic: Quotes and orders are drafts, follow-ups are cancelled, external actions are explicitly cancelled, and teams contain disabled accounts only.
    # Constraints: No executable actions, real completed transactions, or external connections; do not synchronize or modify other companies' priorities.
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

    # Function: Create three synthetic emails and two traceable analysis generations.
    # Inputs: `index`: scenario number; `scene`: company business-object dictionary.
    # Outputs: Extend scene with mailbox, emails, and snapshots.
    # Logic: Use existing rule extraction and templates; store actual fact-schema versions separately from synthetic provenance. Invalidate old snapshots and retain new ones.
    # Constraints: No LLM calls; scores remain unknown and versions isolated. Simulated relations are not real Agent execution records.
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

    # Function: Create announcement examples, internal knowledge, and explicitly simulated citations.
    # Inputs: `index`: scenario number; `scene`: existing company and emails.
    # Outputs: None; register knowledge, announcement examples, chats, vectors, and attachment metadata.
    # Logic: Cycle through 5 event scenarios; store label truth separately in the batch manifest without leaking it into news bodies.
    # Constraints: News and execution records are fictional; synthetic vectors use a separate space and cannot evaluate real semantic retrieval.
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

    # Function: Assemble the entire batch.
    # Inputs: No external parameters; read employee, batch, and count from instance state.
    # Outputs: Dictionary containing exact row manifests, fingerprints, attachments, and separate labels.
    # Logic: Create related data, then reread it from the database to compute fingerprints; create attachments exclusively.
    # Constraints: Delegate rollback to run_seed; register each attachment's actual path immediately after creation for failure cleanup.
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


# Function: Locate the unique database batch manifest.
# Inputs: `actor`: employee; `batch`: complete batch name.
# Outputs: First matching AuditEvent or None; ordinary imports prevent duplicates using the employee lock.
# Logic: Match exact event and object_id, never fuzzy body text.
# Constraints: Command identity checks reject reuse of the same batch by different accounts.
def load_manifest(actor, batch):
    return AuditEvent.objects.filter(owner=actor, event=EVENT, object_id=batch).first()


# Function: Verify that imported fixtures remain complete and unchanged.
# Inputs: `manifest`: batch manifest.
# Outputs: Counts by model; missing rows, drift, or file corruption raise CommandError.
# Logic: Reread all primary keys by model and compare content fingerprints and file digests.
# Constraints: No data mutations or successful partial results; do not call during attachment cleanup.
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


# Function: Detect references from nonfixture records to fixtures.
# Inputs: `manifest`: batch manifest.
# Outputs: List of external-reference descriptions.
# Logic: Inspect actual foreign keys in the current ORM, including automatic many-to-many tables, excluding same-batch rows.
# Constraints: Arbitrary JSON business references cannot be detected automatically; deletion guidance requires batch isolation before manual business extensions.
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


# Function: Import a batch atomically or verify idempotent replay.
# Inputs: `actor`: authorized target account; `batch`: batch; `count`: scenario count.
# Outputs: Database manifest; failed initial imports roll back all business rows.
# Logic: Lock the account; store exact primary keys in an audit event. On attachment errors remove only files actually created by this invocation.
# Constraints: Fail directly when existing batch parameters differ; no automatic retries. Report-write failures after commit do not roll back business data.
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


# Function: Compute manifest deletion order from current relations.
# Inputs: `manifest`: validated batch manifest.
# Outputs: Child-first sequence of models and primary keys.
# Logic: Build a dependency graph from actual foreign keys and topologically sort it, avoiding stale creation order after relation edits.
# Constraints: Report cycles explicitly without breaking relations implicitly; the caller holds manifest and row locks in one transaction.
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


# Function: Preview deletion or perform exact batch cleanup.
# Inputs: `actor`: employee; `batch`: batch; `apply`: explicit execution flag.
# Outputs: Deletion counts or preview details.
# Logic: Lock employee, manifest, and fixture rows; verify fingerprints and reject external references. Delete according to current dependencies, then clean exact attachments after commit.
# Constraints: Retain cleanup state and file manifests after database commit so file failures can be explicitly rerun; no automatic retries or deletion of other batches.
def run_delete(actor, batch, apply):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        entry = load_manifest(actor, batch)
        if not entry:
            raise CommandError("没有找到该账号的批次清单。")
        # Use the same manifest row lock as shared maintenance; reread JSON after waiting so cleanup never uses stale fingerprints.
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


# Function: Provide fixture creation, verification, and subsequent cleanup.
# Logic: Bind an explicit database name; writes require --apply, and nonisolated test databases additionally require --allow-live-database.
# Constraints: Do not switch .env, create permission credentials, or apply migrations; the database must already contain current target tables.
class Command(BaseCommand):
    # Function: Define command arguments.
    # Inputs: `parser`: Django argument parser.
    # Outputs: None; register account, batch, count, operation, and report path.
    # Logic: Default to a seed preview with 100 scenarios; require an explicit complete batch name.
    # Constraints: --apply prevents accidental operations in this tool; it does not replace operator authorization.
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--batch", required=True)
        parser.add_argument("--count", type=int, default=100)
        parser.add_argument("--action", choices=("seed", "verify", "delete"), default="seed")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database-name", required=True)
        parser.add_argument("--allow-live-database", action="store_true")
        parser.add_argument("--report")

    # Function: Validate command context and execute one operation.
    # Inputs: `args`: unused positional arguments; `options`: parsed arguments and local settings.
    # Outputs: Standard-output summary and optional JSON report; failures exit nonzero.
    # Logic: Validate batch, account, and database; reject cross-account marker reuse and distinguish previews from writes explicitly.
    # Constraints: Accept only explicitly named PostgreSQL databases; live writes require an explicit switch, and report failures never implicitly repeat imports.
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
