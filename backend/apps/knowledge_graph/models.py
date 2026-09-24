"""职责：保存业务实体、版本化来源、事实、派生证据及事务变更事件。
实现：源记录保持权威；事实可由多个独立派生支持，每个派生依赖全部输入版本。
关联：projection 创建版本与来源边，sync 消费 PostgreSQL 触发器事件，views 按 owner 查询。
目录：
- Entity：稳定业务实体。
- Entity.Meta：源身份唯一约束。
- SourceVersion：来源字段的不可变快照。
- SourceVersion.Meta：同来源仅一个当前版本。
- Fact：实体关系或属性断言。
- Derivation：一次确定性规则应用及其输入。
- Support：事实与派生的多对多支持关系。
- Support.Meta：防止重复支持边。
- Change：事务内写入的变更事件。
- ProjectionState：每位所有者的图谱构建状态。
- Episode：外部文本及一次经过校验的模型关联结果。
- Episode.Meta：同用户来源键唯一约束。
变量索引：
- Entity.id：稳定 UUID，由 owner、模型及源主键确定。
- Entity.owner：业务归属，删除账号级联清除图谱。
- Entity.kind：来源模型标识。
- Entity.source_id：来源主键的字符串形式。
- Entity.label：当前展示名称，不作为身份。
- Entity.active：源记录是否参与当前图谱。
- Entity.Meta.constraints：所有者与源身份唯一。
- SourceVersion.id：不可变来源版本 UUID。
- SourceVersion.owner：来源归属。
- SourceVersion.kind：来源模型标识。
- SourceVersion.source_id：来源主键。
- SourceVersion.fingerprint：所选字段规范 JSON 的 SHA256。
- SourceVersion.snapshot：白名单字段快照，不含邮箱凭证或完整邮件正文。
- SourceVersion.current：是否对应当前存在的源记录。
- SourceVersion.recorded_at：图谱记录此版本的时间，不冒充业务有效时间。
- SourceVersion.Meta.constraints：当前来源版本的条件唯一约束。
- Fact.id：根据所有者及断言内容生成的稳定 UUID。
- Fact.owner：事实归属。
- Fact.subject：主语实体。
- Fact.predicate：规则定义的关系或属性类型。
- Fact.object：可选宾语实体。
- Fact.value：属性原始值或结构化值；关系事实为 null。
- Fact.origin：structured 或 extraction，不表示人工确认。
- Fact.status：active、unsupported 或 needs_review。
- Derivation.id：规则、断言、证据位置及输入版本组成的稳定 UUID。
- Derivation.owner：派生归属。
- Derivation.rule：明确版本化的映射规则。
- Derivation.inputs：该次派生必须共同成立的来源版本。
- Derivation.evidence：字段路径、版本 ID、可选原文引用及业务观察时间。
- Derivation.active：当前输入是否仍然适用。
- Derivation.created_at：首次记录此派生的时间。
- Support.fact：被支持的事实。
- Support.derivation：独立支持路径。
- Support.Meta.constraints：事实与派生唯一。
- Change.owner_id：待更新的所有者 ID，无外键以兼容账号级联删除。
- Change.kind：触发变更的来源模型或显式 backfill。
- Change.source_id：来源主键，只用于受控审计。
- Change.operation：INSERT、UPDATE、DELETE、TRUNCATE 或 BACKFILL。
- Change.status：pending、completed 或 failed，不隐式重试 failed。
- Change.error_code：失败类型，不存原始异常或业务正文。
- Change.created_at：事件创建时间，不用自增 ID 推断事务提交顺序。
- ProjectionState.owner：每位所有者唯一的状态记录。
- ProjectionState.ready：是否至少完成一次构建。
- ProjectionState.generation：成功构建次数。
- ProjectionState.synced_at：最近成功构建时间。
- Episode.id：外部输入的稳定 UUID。
- Episode.owner：输入及抽取结果的所有者。
- Episode.source_key：调用方提供的不可变来源幂等键。
- Episode.text：输入原文，按业务数据权限保存。
- Episode.observed_at：来源发生时间，由调用方明确提供。
- Episode.extraction：经校验的实体、关系和属性候选。
- Episode.model_audit：实际模型、提示版本及生成参数，不含凭据。
- Episode.created_at：系统接收时间。
- Episode.retracted：显式撤回标志，不覆盖历史证据。
- Episode.Meta.constraints：同用户来源键唯一，防止重复入库。
"""

import uuid
from django.conf import settings
from django.db import models


# 功能：表达源业务记录的稳定身份。
# 逻辑：标签可更新，身份不随名称变化；源归档时停用。
# 约束：不同所有者、不同来源的同名记录不会自动合并。
class Entity(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    label = models.TextField()
    active = models.BooleanField(default=True)

    # 功能：约束源身份唯一。
    # 逻辑：相同 owner、kind、source_id 仅一条实体。
    # 约束：不根据标签去重。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "source_id"], name="kg_entity_source")]


# 功能：保存映射使用的精确来源版本。
# 逻辑：字段内容变更时新增版本；只更新旧版本的 current 标记。
# 约束：快照不覆盖；业务删除后历史仍仅所有者可审计，账号删除级联清除。
class SourceVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField()
    current = models.BooleanField(default=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    # 功能：防止同一来源出现两个当前版本。
    # 逻辑：条件唯一索引仅约束 current=True。
    # 约束：允许历史上内容相同但发生时间不同的版本。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "source_id"], condition=models.Q(current=True), name="kg_current_source")]


# 功能：保存可多来源支持的关系或属性。
# 逻辑：相同断言复用，失去全部当前支持后标记 unsupported。
# 约束：needs_review 仅表示规则发现多个不同候选值，不是模型判定真假。
class Fact(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="outgoing")
    predicate = models.CharField(max_length=100)
    object = models.ForeignKey(Entity, null=True, on_delete=models.CASCADE, related_name="incoming")
    value = models.JSONField(null=True)
    origin = models.CharField(max_length=24)
    status = models.CharField(max_length=24, default="active", db_index=True)


# 功能：表示由全部输入版本共同支持的一次规则应用。
# 逻辑：同一事实可有多个独立派生，任一有效路径可继续支持该事实。
# 约束：本版只执行确定性映射，不调用模型、不修改原抽取的置信度。
class Derivation(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rule = models.CharField(max_length=120)
    inputs = models.ManyToManyField(SourceVersion, related_name="derivations")
    evidence = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：将事实关联到一个独立支持路径。
# 逻辑：历史支持边保留，是否当前成立由派生 active 决定。
# 约束：不同订单的支持不会因其中一单取消而一起删除。
class Support(models.Model):
    fact = models.ForeignKey(Fact, on_delete=models.CASCADE, related_name="supports")
    derivation = models.ForeignKey(Derivation, on_delete=models.CASCADE, related_name="supports")

    # 功能：保证支持边幂等。
    # 逻辑：数据库约束 fact 和 derivation 二元组。
    # 约束：保留跨版本的不同派生。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["fact", "derivation"], name="kg_fact_support")]


# 功能：持久化与业务写入同事务的图谱维护请求。
# 逻辑：Worker 按 pending 状态领取，不按最大序号跳过未提交事件。
# 约束：失败显式保留；仅运维显式操作可重排；不保存完整源行。
class Change(models.Model):
    owner_id = models.BigIntegerField(db_index=True)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    operation = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="pending", db_index=True)
    error_code = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：描述一个用户的图谱读模型是否已建立。
# 逻辑：仅在整次构建成功提交时推进代次与时间。
# 约束：ready 不等于实时最新，查询还必须检查未完成变更。
class ProjectionState(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    ready = models.BooleanField(default=False)
    generation = models.PositiveBigIntegerField(default=0)
    synced_at = models.DateTimeField(null=True)


# 功能：保存外部文本与单次模型处理的不可变来源记录。
# 逻辑：实体引用由服务在当前图谱中校验；数据库捕获事件驱动普通图谱 Worker 投影。
# 约束：模型结果为带证据的陈述，不写入 CRM 交易状态；正文不进入日志，撤回仍保留审计历史。
class Episode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source_key = models.CharField(max_length=200)
    text = models.TextField()
    observed_at = models.DateTimeField()
    extraction = models.JSONField()
    model_audit = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    retracted = models.BooleanField(default=False)

    # 功能：约束同用户外部来源只接收一次。
    # 逻辑：数据库唯一键与服务的内容比较共同实现幂等；内容改变须使用新来源键。
    # 约束：不同用户可使用相同来源键，不能借此共享数据。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "source_key"], name="kg_episode_source")]
