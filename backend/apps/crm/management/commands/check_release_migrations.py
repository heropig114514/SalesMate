"""职责：提供可手动执行的保守数据库迁移审查。
实现：检查实际待执行迁移，只放行新表、新索引、可空非唯一新增字段及 vector 扩展创建。
关联：自动部署不再调用本命令；运维可按需手动运行；不代替业务级新旧版本兼容审查，DDL 锁等待另由 PGOPTIONS 限制。
目录：
- compatible：判定一个迁移操作是否符合保守在线迁移建议。
- Command：检查实际数据库待执行迁移。
- Command.handle：拒绝逆向或不在允许集合中的操作。
变量索引：
- Command.help：命令用途。
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from pgvector.django import VectorExtension


# 功能：识别手动审查所用的保守操作集合。
# 输入：`operation` 为 Django 迁移操作。
# 输出：布尔值。
# 逻辑：只接受精确类型以阻止自定义子类藏入任意 SQL；新增字段须可空且无唯一约束。
# 约束：Alter/Remove/Rename/RunPython/RunSQL 默认拒绝，仅影响显式运行本诊断的退出码，不阻断自动部署。
def compatible(operation):
    if type(operation) in {migrations.CreateModel, migrations.AddIndex, VectorExtension}:
        return True
    return type(operation) is migrations.AddField and operation.field.null and not operation.field.unique and not operation.field.primary_key


# 功能：供运维显式检查待执行迁移是否符合保守建议。
# 逻辑：仅手动调用时读取迁移图和数据库记录，不执行迁移或参与自动发布。
# 约束：依赖真实目标数据库状态。
class Command(BaseCommand):
    help = "手动检查迁移是否符合保守在线发布建议；不参与自动部署。"

    # 功能：检查待执行迁移并列出不兼容项。
    # 输入：`args`/`options` 为命令参数，无自定义参数；读取默认数据库。
    # 输出：成功信息或 CommandError，不写数据库。
    # 逻辑：计算到所有叶节点的计划，逐项调用 compatible。
    # 约束：不通过修改已应用迁移来绕过兼容检查。
    def handle(self, *args, **options):
        executor = MigrationExecutor(connection)
        blocked = []
        for migration, backwards in executor.migration_plan(executor.loader.graph.leaf_nodes()):
            for operation in migration.operations:
                if backwards or not compatible(operation):
                    blocked.append(f"{migration.app_label}.{migration.name}:{type(operation).__name__}")
        if blocked:
            raise CommandError("Online deployment blocked; explicit migration review required: " + ", ".join(blocked))
        self.stdout.write("Pending migrations satisfy the conservative online release gate.")
