"""职责：在已部署代码旁加载独立夹具命令，并在恢复副本验证导入与清理。
实现：显式指定应用、模块及目标库；自检仅接受专名副本，实际执行由 seed_kg_lab.Command 校验。
关联：seed_kg_lab.py；部署服务器无需替换代码版本或重启 Web/Worker。
目录：
- counts：读取应用模型行数。
- self_test：验证幂等、权限、页面、漂移防护、外部引用与完整清理。
- self_test.fail_second：模拟第二组写入失败以验证事务回滚。
- main：装载部署环境并分派自检或管理命令。
变量索引：
- 无
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


# 功能：统计业务应用各表行数。
# 输入：无外部参数；读取当前 Django 注册表和显式选择的数据库。
# 输出：模型名称到行数的映射。
# 逻辑：排除框架表，仅统计已部署的业务模型。
# 约束：只读，用于断言副本数据在测试清理后未发生变化。
def counts():
    from django.apps import apps
    return {m._meta.label: m.objects.count() for m in apps.get_models()
            if m._meta.app_label in {"accounts", "crm", "sales", "chat", "vectors", "agent_tools"}}


# 功能：在已恢复的隔离库执行真实 ORM 和 HTTP 投影测试。
# 输入：`module` 夹具模块、`username` 副本中既有账号。
# 输出：校验结果字典；断言失败或 ORM 错误向上抛出。
# 逻辑：注入中途失败验证回滚；导入两组，验证重放、读取、权限、修改与引用阻断，最终删除。
# 约束：不调用 LLM 或外部服务；必须在指定名称的副本，附件写入临时目录。
def self_test(module, username):
    from django.apps import apps
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management.base import CommandError
    from django.db import connection
    from django.test import override_settings
    from rest_framework.test import APIClient

    if not connection.settings_dict["NAME"].startswith("salesmate_kg_restore_"):
        raise RuntimeError("自检只接受 salesmate_kg_restore_ 前缀的独立副本。")
    actor = get_user_model().objects.get(username=username)
    before = counts()
    with tempfile.TemporaryDirectory(prefix="kg-seed-test-") as folder:
        with override_settings(BASE_DIR=Path(folder), ALLOWED_HOSTS=[*settings.ALLOWED_HOSTS, "testserver"]):
            original = module.FixtureBuilder.knowledge_and_chat
            # 功能：模拟第二组资料生成失败。
            # 输入：`builder` 夹具实例、`index` 当前组号、`scene` 客户上下文。
            # 输出：第一组调用原实现；第二组抛显式异常。
            # 逻辑：验证已有多表写入会随外围事务完整回滚。
            # 约束：只在隔离库替换生成器方法，原实现由 patch 自动恢复。
            def fail_second(builder, index, scene):
                if index == 2:
                    raise RuntimeError("intentional rollback probe")
                return original(builder, index, scene)

            with patch.object(module.FixtureBuilder, "knowledge_and_chat", fail_second):
                try:
                    module.run_seed(actor, "KGSEED_20260921_ROLLBACK", 2)
                except RuntimeError as error:
                    assert str(error) == "intentional rollback probe"
                else:
                    raise AssertionError("中途失败没有传播。")
            assert counts() == before, "失败后数据库记录未完全回滚。"
            batch = "KGSEED_20260921_TEST"
            manifest = module.run_seed(actor, batch, 2)
            assert all(n >= 2 for n in manifest["table_counts"].values())
            assert len(manifest["table_counts"]) >= 40
            assert module.run_seed(actor, batch, 2) == manifest
            module.verify_manifest(manifest)
            company = apps.get_model("crm.Company").objects.get(pk=manifest["truth"][0]["company_id"])
            client = APIClient()
            client.force_authenticate(actor)
            for url in ("/api/v1/companies/", f"/api/v1/companies/{company.pk}/", "/api/v1/sales/records/products/"):
                response = client.get(url)
                assert response.status_code == 200, (url, response.status_code)
            other = get_user_model().objects.filter(is_active=True).exclude(pk=actor.pk).first()
            client.force_authenticate(other)
            assert client.get(f"/api/v1/companies/{company.pk}/").status_code == 404
            product = apps.get_model("sales.Product").objects.get(pk=next(r["pk"] for r in manifest["rows"] if r["model"] == "sales.Product"))
            original_name = product.name
            type(product).objects.filter(pk=product.pk).update(name="Edited fixture")
            try:
                module.run_delete(actor, batch, False)
            except CommandError as error:
                assert "修改" in str(error)
            else:
                raise AssertionError("修改后的夹具未阻止删除。")
            type(product).objects.filter(pk=product.pk).update(name=original_name)
            contact = apps.get_model("crm.Contact").objects.create(company=company, email="outside@example.com", name="outside fixture")
            try:
                module.run_delete(actor, batch, False)
            except CommandError as error:
                assert "其他记录引用" in str(error)
            else:
                raise AssertionError("外部引用未阻止删除。")
            contact.delete()
            preview = module.run_delete(actor, batch, False)
            assert preview["action"] == "delete_preview"
            deleted = module.run_delete(actor, batch, True)
            assert deleted["rows"] == len(manifest["rows"])
            assert counts() == before, "清理后副本原有记录数量发生变化。"
            assert not list(Path(folder).rglob("*.txt")), "附件未删除。"
    return {"status": "passed", "models": len(manifest["table_counts"]), "fixture_rows": len(manifest["rows"]),
            "checks": ["transaction rollback", "idempotent replay", "record and file fingerprints", "business HTTP projections",
                       "cross-account isolation", "modified-row deletion guard", "external-reference deletion guard", "exact database/file cleanup"]}


# 功能：加载指定应用和夹具模块。
# 输入：命令行 argv、部署应用的 .env；只输出非敏感测试或导入摘要。
# 输出：自检 JSON 或管理命令摘要；失败非零退出。
# 逻辑：可显式切换到恢复副本，默认保留部署数据库；副本附件目录与线上隔离。
# 约束：--self-test 必须结合 --test-db；所有线上命令仍需精确数据库名及写入开关。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--seed-module", required=True)
    parser.add_argument("--test-db")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--test-username", default="tst1")
    options, remainder = parser.parse_known_args()
    root = Path(options.app_root).resolve()
    os.chdir(root)
    sys.path[:0] = [str(root / "backend"), str(root)]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection
    if options.test_db:
        if not options.test_db.startswith("salesmate_kg_restore_"):
            raise RuntimeError("恢复副本名称前缀不合法。")
        connection.close()
        connection.settings_dict["NAME"] = options.test_db
        settings.BASE_DIR = Path(options.seed_module).resolve().parent / "validation-files"
    if options.self_test and not options.test_db:
        raise RuntimeError("未明确选择恢复副本，拒绝自检。")
    spec = importlib.util.spec_from_file_location("kg_seed_module", options.seed_module)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if options.self_test:
        print(json.dumps(self_test(module, options.test_username), ensure_ascii=False))
    else:
        module.Command().run_from_argv(["manage.py", "seed_kg_lab", *remainder])


if __name__ == "__main__":
    main()
