"""职责：验证向量隔离、执行模式和在线迁移门禁。
实现：向量测试使用真实 PostgreSQL 扩展；消息边界使用模拟 broker，不调用真实邮件或模型。
关联：vectors.services、common.execution/tasks、check_release_migrations。
目录：
- VectorTests：实际向量读写测试。
- VectorTests.setUp：创建隔离员工。
- VectorTests.test_search_isolates_owner_model_and_dimension：验证相似度及归属隔离。
- VectorTests.test_update_and_dimension_change：验证更新和模型维度契约。
- VectorTests.test_invalid_vectors_and_inactive_owner：验证非法输入和停用员工。
- ExecutionTests：任务传输和迁移门禁测试。
- ExecutionTests.test_local_mode_never_contacts_broker：本地执行不接触消息服务。
- ExecutionTests.test_remote_only_sends_identifiers：远程仅发送数据库标识。
- ExecutionTests.test_publish_failure_does_not_run_locally：发送失败无本地回退。
- ExecutionTests.test_sales_remote_waits_without_local_side_effect：销售只调用一次远程任务。
- ExecutionTests.test_release_gate_rejects_destructive_operations：拒绝破坏性迁移。
变量索引：
- 无
"""
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from apps.vectors.services import put_document, search_documents, validate_vector
from apps.vectors.models import VectorDocument
from apps.crm.management.commands.check_release_migrations import compatible
from apps.crm.worker import run_sync
from common.execution import CeleryExecutor, execute_sales, work_executor


# 功能：验证真实向量 SQL 的检索与写入边界。
# 逻辑：使用测试数据库和合成文本，不依赖嵌入服务。
# 约束：通过不能证明真实业务嵌入质量。
class VectorTests(TestCase):
    # 功能：建立两个互相隔离的员工。
    # 输入：测试数据库。
    # 输出：保存 left/right 员工实例。
    # 逻辑：不创建邮箱或外部凭证。
    # 约束：事务由测试框架回滚。
    def setUp(self):
        self.left = get_user_model().objects.create_user(username="vector-left")
        self.right = get_user_model().objects.create_user(username="vector-right")

    # 功能：验证相似度排序不会读到其他员工或模型的数据。
    # 输入：合成两个模型和两位员工文档。
    # 输出：仅当前员工同模型近邻，距离为预期值。
    # 逻辑：使用正交向量使真实 SQL 的余弦结果可核验。
    # 约束：不模拟数据库和 vector 运算。
    def test_search_isolates_owner_model_and_dimension(self):
        for owner, model, source, vector in [(self.left, "m1", "near", [1, 0]), (self.left, "m1", "far", [0, 1]), (self.right, "m1", "secret", [1, 0]), (self.left, "m2", "other", [1, 0, 0])]:
            put_document(owner=owner, namespace="test", model=model, source=source, content=source, embedding=vector)
        result = search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0])
        self.assertEqual([item["source"] for item in result], ["near", "far"])
        self.assertAlmostEqual(result[0]["distance"], 0)
        self.assertAlmostEqual(result[1]["distance"], 1)
        self.assertEqual(search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0, 0]), [])

    # 功能：验证文档更新不制造重复，维度变更须显式换模型版本。
    # 输入：同来源同模型的两次写入。
    # 输出：单条新文本记录与对应摘要，维度变更失败。
    # 逻辑：查询真实唯一约束和保存结果。
    # 约束：不自动迁移已有嵌入。
    def test_update_and_dimension_change(self):
        args = dict(owner=self.left, namespace="test", model="m1", source="one", embedding=[1, 0])
        first = put_document(**args, content="old")
        second = put_document(**args, content="new")
        self.assertEqual(first.pk, second.pk)
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.assertEqual(VectorDocument.objects.count(), 1)
        with self.assertRaises(ValueError):
            put_document(**{**args, "embedding": [1, 0, 0]}, content="new")

    # 功能：拒绝非法向量与停用员工访问。
    # 输入：零、非有限、过大向量及停用员工。
    # 输出：ValueError 或用户不存在错误。
    # 逻辑：直接验证输入和服务层权限检查。
    # 约束：不将失败转换为空搜索结果。
    def test_invalid_vectors_and_inactive_owner(self):
        for vector in ([], [0, 0], [float("nan")], [float("inf")], [1e100]):
            with self.assertRaises(ValueError):
                validate_vector(vector)
        self.left.is_active = False
        self.left.save(update_fields=["is_active"])
        with self.assertRaises(get_user_model().DoesNotExist):
            search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0])


# 功能：验证任务适配与发布边界。
# 逻辑：模拟消息传输，检查结果等待、错误传播与参数白名单。
# 约束：真实 Redis 消息往返由 CI 和部署 check_infrastructure 另行验证。
class ExecutionTests(SimpleTestCase):
    # 功能：验证本地模式保持原执行方式。
    # 输入：local 显式配置及无副作用函数。
    # 输出：线程结果，broker 从未调用。
    # 逻辑：实际进入线程执行器。
    # 约束：无需本地 Redis 或 Celery 服务。
    @override_settings(TASK_EXECUTION_MODE="local")
    def test_local_mode_never_contacts_broker(self):
        with patch("common.tasks.execute.apply_async") as publish:
            with work_executor(1, "test") as executor:
                self.assertEqual(executor.submit(int, "7").result(), 7)
            publish.assert_not_called()

    # 功能：确保远程消息不包含员工对象或授权码。
    # 输入：员工主键和模拟 Celery 完成结果。
    # 输出：严格 sync 类型和整数主键，结果被清理。
    # 逻辑：检查一次发布和上下文排空。
    # 约束：不触发真实同步。
    def test_remote_only_sends_identifiers(self):
        with patch("common.tasks.execute.apply_async") as publish:
            publish.return_value.get.return_value = True
            publish.return_value.ready.return_value = True
            with CeleryExecutor() as executor:
                self.assertTrue(executor.submit(run_sync, Mock(pk=6)).result())
            publish.assert_called_once_with(args=["sync", 6], queue="crm", retry=False)
            publish.return_value.forget.assert_called_once_with()

    # 功能：保证基础设施错误不引入隐式回退。
    # 输入：模拟 broker 发布失败。
    # 输出：原异常向上传播。
    # 逻辑：执行器提交路径不调用业务函数。
    # 约束：不自动重试。
    def test_publish_failure_does_not_run_locally(self):
        with patch("common.tasks.execute.apply_async", side_effect=ConnectionError("synthetic")) as publish:
            with self.assertRaises(ConnectionError), CeleryExecutor() as executor:
                executor.submit(run_sync, Mock(pk=6))
            self.assertEqual(publish.call_count, 1)

    # 功能：保证销售任务不会在远程成功后本地再次执行。
    # 输入：celery 模式、动作标识和模拟结果。
    # 输出：一次发布、本地函数零调用。
    # 逻辑：等待远程结果后清理。
    # 约束：不发送邮件。
    @override_settings(TASK_EXECUTION_MODE="celery")
    def test_sales_remote_waits_without_local_side_effect(self):
        local = Mock()
        with patch("common.tasks.execute.apply_async") as publish:
            publish.return_value.ready.return_value = True
            execute_sales("action-id", local)
            publish.assert_called_once_with(args=["sales", "action-id"], queue="sales", retry=False)
            publish.return_value.get.assert_called_once_with()
        local.assert_not_called()

    # 功能：阻止破坏性及任意代码迁移进入在线发布。
    # 输入：真实 Django 迁移操作实例。
    # 输出：新表与可空字段通过，删除、SQL、非空字段拒绝。
    # 逻辑：直接调用与部署共用的门禁判定。
    # 约束：不会执行传入的 SQL。
    def test_release_gate_rejects_destructive_operations(self):
        self.assertTrue(compatible(migrations.CreateModel("NewTable", [])))
        self.assertTrue(compatible(migrations.AddField("item", "extra", models.TextField(null=True))))
        for operation in (migrations.DeleteModel("Item"), migrations.RunSQL("SELECT 1"), migrations.AddField("item", "extra", models.TextField())):
            self.assertFalse(compatible(operation))
