"""职责：校验销售业务接口与关系引用，并生成明确的 OpenAPI 字段。
实现：显式字段白名单、只读状态保护和授权关系查询；商机接收规范产品名称，金额计算使用 Decimal。
关联：views 选择具体序列化器，services 再执行事务、跨实体和状态校验。
目录：
- ConnectionSerializer：连接安全字段。
- ConnectionSerializer.Meta：字段配置。
- StrictModelSerializer：拒绝未知或只读输入并按用户限制关系。
- StrictModelSerializer.to_internal_value：拒绝未经声明的写入。
- StrictModelSerializer.get_fields：限制关系字段可引用的对象。
- DocumentSerializer：为报价和订单输出金额与行项目。
- DocumentSerializer.get_total：计算当前有效行项目净额。
- DocumentSerializer.get_lines：返回行项目快照。
- CompanySettingsSerializer：客户生命周期和人工主要联系人设置的授权字段契约。
- CompanySettingsSerializer.Meta：声明本实体字段和不可直接写入的状态。
- CompanyAliasSerializer：人工确认的域名或联系人归组映射的授权字段契约。
- CompanyAliasSerializer.Meta：声明本实体字段和不可直接写入的状态。
- ContactProfileSerializer：人工联系人补充资料的授权字段契约。
- ContactProfileSerializer.Meta：声明本实体字段和不可直接写入的状态。
- TeamSerializer：拥有明确管理者的业务团队的授权字段契约。
- TeamSerializer.Meta：声明本实体字段和不可直接写入的状态。
- MembershipSerializer：团队成员及角色的授权字段契约。
- MembershipSerializer.Meta：声明本实体字段和不可直接写入的状态。
- CompanyGrantSerializer：公司业务记录共享授权的授权字段契约。
- CompanyGrantSerializer.Meta：声明本实体字段和不可直接写入的状态。
- ProductSerializer：商品目录与人工库存记录的授权字段契约。
- ProductSerializer.Meta：声明本实体字段和不可直接写入的状态。
- TicketSerializer：客户服务工单的授权字段契约。
- TicketSerializer.Meta：声明本实体字段和不可直接写入的状态。
- OpportunitySerializer：销售商机与管线的授权字段契约。
- OpportunitySerializer.Meta：声明本实体字段和不可直接写入的状态。
- QuoteSerializer：有审核与真实外发证据的报价单的授权字段契约。
- QuoteSerializer.Meta：声明本实体字段和不可直接写入的状态。
- QuoteLineSerializer：报价明细快照的授权字段契约。
- QuoteLineSerializer.Meta：声明本实体字段和不可直接写入的状态。
- SalesOrderSerializer：客户订单及确认状态的授权字段契约。
- SalesOrderSerializer.Meta：声明本实体字段和不可直接写入的状态。
- OrderLineSerializer：订单明细快照的授权字段契约。
- OrderLineSerializer.Meta：声明本实体字段和不可直接写入的状态。
- FollowUpSerializer：客户跟进与到期提醒的授权字段契约。
- FollowUpSerializer.Meta：声明本实体字段和不可直接写入的状态。
- ConversationSerializer：员工自己的通用或客户助手会话的授权字段契约。
- ConversationSerializer.Meta：声明本实体字段和不可直接写入的状态。
- MessageSerializer：不可变会话消息的授权字段契约。
- MessageSerializer.Meta：声明本实体字段和不可直接写入的状态。
- DraftSerializer：私有会话中的可编辑草稿的授权字段契约。
- DraftSerializer.Meta：声明本实体字段和不可直接写入的状态。
- ToolActionSerializer：明确确认的外部工具动作与执行状态的授权字段契约。
- ToolActionSerializer.Meta：声明本实体字段和不可直接写入的状态。
- AttachmentSerializer：员工私有文件及客户关联的授权字段契约。
- AttachmentSerializer.Meta：声明本实体字段和不可直接写入的状态。
- NotificationSerializer：应用内到期提醒的授权字段契约。
- NotificationSerializer.Meta：声明本实体字段和不可直接写入的状态。
变量索引：
- OpportunitySerializer.product_names：规范产品名称的显式数组；省略沿用原值，null 或空数组表示未知。
- ConnectionSerializer.Meta.model：连接模型。
- ConnectionSerializer.Meta.fields：无凭证字段清单。
- ConnectionSerializer.Meta.read_only_fields：所有字段只读。
- DocumentSerializer.total：以字符串返回的单币种净额，不含税费。
- DocumentSerializer.lines：当前有效行项目列表。
- SERIALIZERS：业务路由名到具体序列化器的白名单。
- CompanySettingsSerializer.Meta.model：对应 CompanySettings 关系模型。
- CompanySettingsSerializer.Meta.fields：明确允许返回及校验的字段集合。
- CompanySettingsSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- CompanyAliasSerializer.Meta.model：对应 CompanyAlias 关系模型。
- CompanyAliasSerializer.Meta.fields：明确允许返回及校验的字段集合。
- CompanyAliasSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- ContactProfileSerializer.Meta.model：对应 ContactProfile 关系模型。
- ContactProfileSerializer.Meta.fields：明确允许返回及校验的字段集合。
- ContactProfileSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- TeamSerializer.Meta.model：对应 Team 关系模型。
- TeamSerializer.Meta.fields：明确允许返回及校验的字段集合。
- TeamSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- MembershipSerializer.Meta.model：对应 Membership 关系模型。
- MembershipSerializer.Meta.fields：明确允许返回及校验的字段集合。
- MembershipSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- CompanyGrantSerializer.Meta.model：对应 CompanyGrant 关系模型。
- CompanyGrantSerializer.Meta.fields：明确允许返回及校验的字段集合。
- CompanyGrantSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- ProductSerializer.Meta.model：对应 Product 关系模型。
- ProductSerializer.Meta.fields：明确允许返回及校验的字段集合。
- ProductSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- TicketSerializer.Meta.model：对应 Ticket 关系模型。
- TicketSerializer.Meta.fields：明确允许返回及校验的字段集合。
- TicketSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- OpportunitySerializer.Meta.model：对应 Opportunity 关系模型。
- OpportunitySerializer.Meta.fields：明确允许返回及校验的字段集合。
- OpportunitySerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- QuoteSerializer.Meta.model：对应 Quote 关系模型。
- QuoteSerializer.Meta.fields：明确允许返回及校验的字段集合。
- QuoteSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- QuoteLineSerializer.Meta.model：对应 QuoteLine 关系模型。
- QuoteLineSerializer.Meta.fields：明确允许返回及校验的字段集合。
- QuoteLineSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- SalesOrderSerializer.Meta.model：对应 SalesOrder 关系模型。
- SalesOrderSerializer.Meta.fields：明确允许返回及校验的字段集合。
- SalesOrderSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- OrderLineSerializer.Meta.model：对应 OrderLine 关系模型。
- OrderLineSerializer.Meta.fields：明确允许返回及校验的字段集合。
- OrderLineSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- FollowUpSerializer.Meta.model：对应 FollowUp 关系模型。
- FollowUpSerializer.Meta.fields：明确允许返回及校验的字段集合。
- FollowUpSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- ConversationSerializer.Meta.model：对应 Conversation 关系模型。
- ConversationSerializer.Meta.fields：明确允许返回及校验的字段集合。
- ConversationSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- MessageSerializer.Meta.validators：消息幂等键由事务检查，允许相同内容重复提交。
- MessageSerializer.Meta.model：对应 Message 关系模型。
- MessageSerializer.Meta.fields：明确允许返回及校验的字段集合。
- MessageSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- DraftSerializer.Meta.model：对应 Draft 关系模型。
- DraftSerializer.Meta.fields：明确允许返回及校验的字段集合。
- DraftSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- ToolActionSerializer.Meta.model：对应 ToolAction 关系模型。
- ToolActionSerializer.Meta.fields：明确允许返回及校验的字段集合。
- ToolActionSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- AttachmentSerializer.Meta.model：对应 Attachment 关系模型。
- AttachmentSerializer.Meta.fields：明确允许返回及校验的字段集合。
- AttachmentSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
- NotificationSerializer.Meta.model：对应 Notification 关系模型。
- NotificationSerializer.Meta.fields：明确允许返回及校验的字段集合。
- NotificationSerializer.Meta.read_only_fields：服务层维护的身份、版本及执行状态。
"""

from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers as s

from apps.crm.models import Company, Contact
from . import models
from .permissions import scope, visible_company_ids


# 功能：严格控制销售接口输入和关系引用。
# 逻辑：白名单之外的字段明确报错，关系对象通过用户授权查询。
# 约束：状态变化和跨实体一致性不依赖序列化器单独保证。
class StrictModelSerializer(s.ModelSerializer):
    # 功能：拒绝未经声明的写入。
    # 输入：`data` 为客户端 JSON 对象。
    # 输出：经 DRF 类型转换的数据；未知或只读字段抛 ValidationError。
    # 逻辑：先检查集合，再调用 ModelSerializer 的常规校验。
    # 约束：不静默忽略 owner、状态或拼错的参数。
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise s.ValidationError("请求必须是 JSON 对象。")
        allowed = {name for name, field in self.fields.items() if not field.read_only}
        invalid = set(data) - allowed
        if invalid:
            raise s.ValidationError(
                {"fields": "字段不可写或未定义：" + ", ".join(sorted(invalid))}
            )
        return super().to_internal_value(data)

    # 功能：限制关系字段可引用的对象。
    # 输入：无参数；读取 serializer.context.request.user。
    # 输出：经过授权过滤的字段字典。
    # 逻辑：客户与联系人使用公司业务权限，其他模型调用 scope，负责人限当前用户和共同团队成员；成员邀请可引用有效账号，其授权由事务验证。
    # 约束：Schema 生成无用户时所有关系查询为空，不执行业务数据枚举。
    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = request.user if request else None
        for field in fields.values():
            if (
                not isinstance(field, s.PrimaryKeyRelatedField)
                or field.queryset is None
            ):
                continue
            model = field.queryset.model
            if user is None or not user.is_authenticated:
                field.queryset = model.objects.none()
            elif model is Company:
                field.queryset = Company.objects.filter(
                    pk__in=visible_company_ids(user)
                )
            elif model is Contact:
                field.queryset = Contact.objects.filter(
                    company_id__in=visible_company_ids(user)
                )
            elif model is get_user_model() and self.Meta.model is models.Membership:
                field.queryset = model.objects.filter(is_active=True)
            elif model is get_user_model():
                teams = scope(models.Team, user).filter(archived=False)
                field.queryset = (
                    model.objects.filter(is_active=True)
                    .filter(
                        Q(pk=user.pk)
                        | Q(
                            sales_memberships__team__in=teams,
                            sales_memberships__archived=False,
                        )
                        | Q(pk__in=teams.values("owner_id"))
                    )
                    .distinct()
                )
            else:
                field.queryset = scope(model, user).filter(archived=False)
        return fields


# 功能：输出单据金额和有效明细。
# 逻辑：所有计算保留 Decimal 精度，逐行四舍五入到两位小数后求和。
# 约束：金额是同一单据币种的折扣后净额，不包括未声明税费。
class DocumentSerializer(StrictModelSerializer):
    total = s.SerializerMethodField()
    lines = s.SerializerMethodField()

    # 功能：计算当前有效行项目净额。
    # 输入：`obj` 为 Quote 或 SalesOrder。
    # 输出：两位小数字符串。
    # 逻辑：数量乘单价减整行折扣，各行使用 ROUND_HALF_UP。
    # 约束：不跨币种、不使用浮点、不推算税费。
    def get_total(self, obj) -> str:
        return str(
            sum(
                (
                    (line.quantity * line.unit_price - line.discount).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    for line in obj.lines.filter(archived=False)
                ),
                Decimal("0.00"),
            )
        )

    # 功能：返回行项目快照。
    # 输入：`obj` 为报价或订单。
    # 输出：序列化后的有效行数组。
    # 逻辑：按创建时间和 UUID 稳定排序。
    # 约束：父单据须已授权，不重新从目录覆盖历史单价。
    def get_lines(self, obj) -> list[dict]:
        serializer = (
            QuoteLineSerializer
            if isinstance(obj, models.Quote)
            else OrderLineSerializer
        )
        return serializer(
            obj.lines.filter(archived=False).order_by("created_at", "id"),
            many=True,
            context=self.context,
        ).data


# 功能：声明客户生命周期和人工主要联系人设置的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class CompanySettingsSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.CompanySettings
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "primary_contact",
            "notes",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明人工确认的域名或联系人归组映射的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class CompanyAliasSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.CompanyAlias
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "group_key",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明人工联系人补充资料的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class ContactProfileSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.ContactProfile
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "contact",
            "title",
            "phone",
            "notes",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明拥有明确管理者的业务团队的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class TeamSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Team
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "name",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明团队成员及角色的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class MembershipSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Membership
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "team",
            "user",
            "role",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明公司业务记录共享授权的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class CompanyGrantSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.CompanyGrant
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "team",
            "role",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明商品目录与人工库存记录的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class ProductSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Product
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "sku",
            "name",
            "description",
            "currency",
            "unit_price",
            "stock_quantity",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明客户服务工单的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class TicketSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Ticket
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "status",
            "priority",
            "due_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# 功能：声明销售商机与管线的字段契约。
# 逻辑：关系字段按当前用户过滤，商机产品名称显式录入，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class OpportunitySerializer(StrictModelSerializer):
    product_names = s.ListField(child=s.CharField(max_length=240), required=False, allow_empty=True, allow_null=True)
    # 功能：绑定模型和接口字段。
    # 逻辑：显式开放产品名称和已有商机字段，不允许浏览器改变 owner 或状态。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Opportunity
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "status",
            "amount",
            "currency",
            "expected_close",
            "product_names",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# 功能：声明有审核与真实外发证据的报价单的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class QuoteSerializer(DocumentSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Quote
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "number",
            "status",
            "currency",
            "valid_until",
            "notes",
            "sent_at",
            "external_message_id",
            "total",
            "lines",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "sent_at",
            "external_message_id",
        ]


# 功能：声明报价明细快照的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class QuoteLineSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.QuoteLine
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "quote",
            "product",
            "description",
            "quantity",
            "unit_price",
            "discount",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明客户订单及确认状态的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class SalesOrderSerializer(DocumentSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.SalesOrder
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "number",
            "quote",
            "currency",
            "status",
            "notes",
            "confirmed_at",
            "total",
            "lines",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "confirmed_at",
        ]


# 功能：声明订单明细快照的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class OrderLineSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.OrderLine
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "order",
            "product",
            "description",
            "quantity",
            "unit_price",
            "discount",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明客户跟进与到期提醒的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class FollowUpSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.FollowUp
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "due_at",
            "status",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# 功能：声明员工自己的通用或客户助手会话的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class ConversationSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Conversation
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "title",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明不可变会话消息的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class MessageSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Message
        validators = []
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "conversation",
            "role",
            "content",
            "client_key",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "role",
        ]


# 功能：声明私有会话中的可编辑草稿的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class DraftSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Draft
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "conversation",
            "kind",
            "subject",
            "content",
            "recipients",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# 功能：声明明确确认的外部工具动作与执行状态的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class ToolActionSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.ToolAction
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "conversation",
            "tool",
            "parameters",
            "status",
            "idempotency_key",
            "approved_at",
            "started_at",
            "finished_at",
            "result",
            "error",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "approved_at",
            "started_at",
            "finished_at",
            "result",
            "error",
        ]


# 功能：声明员工私有文件及客户关联的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class AttachmentSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Attachment
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "name",
            "content_type",
            "size",
            "sha256",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "size",
            "sha256",
        ]


# 功能：声明应用内到期提醒的字段契约。
# 逻辑：关系字段按当前用户过滤，状态由专门业务动作维护。
# 约束：不接受客户端指定 owner、revision 或伪造执行结果。
class NotificationSerializer(StrictModelSerializer):
    # 功能：绑定模型和接口字段。
    # 逻辑：显式字段列表确保新增模型字段不会自动暴露。
    # 约束：跨字段规则由事务服务继续校验。
    class Meta:
        model = models.Notification
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "follow_up",
            "source_revision",
            "title",
            "read_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "read_at",
            "follow_up",
            "source_revision",
            "title",
        ]


# 功能：公开连接身份及状态。
# 逻辑：凭证字段完全排除，连接仅通过 OAuth 回调写入。
# 约束：密文也不返回客户端。
class ConnectionSerializer(StrictModelSerializer):
    # 功能：声明无凭证的字段白名单。
    # 逻辑：全部字段只读，停用经专用版本化归档。
    # 约束：不能通过记录接口伪造连接。
    class Meta:
        model = models.Connection
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "provider",
            "account",
        ]
        read_only_fields = fields


SERIALIZERS = {
    "connections": ConnectionSerializer,
    "customers": CompanySettingsSerializer,
    "aliases": CompanyAliasSerializer,
    "contact-profiles": ContactProfileSerializer,
    "teams": TeamSerializer,
    "memberships": MembershipSerializer,
    "grants": CompanyGrantSerializer,
    "products": ProductSerializer,
    "tickets": TicketSerializer,
    "opportunities": OpportunitySerializer,
    "quotes": QuoteSerializer,
    "quote-lines": QuoteLineSerializer,
    "orders": SalesOrderSerializer,
    "order-lines": OrderLineSerializer,
    "follow-ups": FollowUpSerializer,
    "conversations": ConversationSerializer,
    "messages": MessageSerializer,
    "drafts": DraftSerializer,
    "actions": ToolActionSerializer,
    "files": AttachmentSerializer,
    "notifications": NotificationSerializer,
}
