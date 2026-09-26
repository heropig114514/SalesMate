"""Responsibility: Load a standalone fixture command alongside deployed code and verify import and cleanup in a restored replica.
Implementation: Explicitly selects application, module, and target database; self-test accepts only a specifically named replica, and seed_kg_lab.Command validates actual execution.
Relationships: seed_kg_lab.py; the deployment server need not replace code versions or restart Web or Worker.
Directory:
- counts: Read application-model row counts.
- self_test: Verify idempotency, permissions, pages, drift protection, external references, and complete cleanup.
- self_test.fail_second: Simulate second-set write failure to verify transaction rollback.
- main: Load the deployment environment and dispatch self-test or management command.
Variable index:
- None
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


# Function: Count rows in every table of a business application.
# Inputs: No external parameters; reads the current Django registry and explicitly selected database.
# Outputs: Mapping of model names to row counts.
# Logic: Excludes framework tables and counts only deployed business models.
# Constraints: Read-only; used to assert that replica data is unchanged after test cleanup.
def counts():
    from django.apps import apps
    return {m._meta.label: m.objects.count() for m in apps.get_models()
            if m._meta.app_label in {"accounts", "crm", "sales", "chat", "vectors", "agent_tools"}}


# Function: Run real ORM and HTTP projection tests in a restored isolated database.
# Inputs: `module` is the fixture module; `username` is an existing account in the replica.
# Outputs: Verification-result dictionary; assertion failures or ORM errors propagate.
# Logic: Injects an intermediate failure to verify rollback; imports two sets, verifies replay, reads, authorization, modification, and reference blocking, then deletes them.
# Constraints: Does not call LLMs or external services; must run in the specifically named replica and writes attachments to a temporary directory.
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
# Function: Simulate generation failure for the second data set.
# Inputs: `builder` is the fixture instance, `index` is the current set number, and `scene` is customer context.
# Outputs: Calls the original implementation for the first set; raises an explicit exception for the second.
# Logic: Verifies that existing multi-table writes fully roll back with the enclosing transaction.
# Constraints: Replaces the generator method only in the isolated database; patch restores the original implementation automatically.
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


# Function: Load the specified application and fixture module.
# Inputs: Command-line argv and the deployed application's .env; outputs only non-sensitive test or import summaries.
# Outputs: Self-test JSON or management-command summary; failures exit nonzero.
# Logic: Can explicitly switch to a restored replica while preserving the deployment database by default; replica attachment directories are isolated from production.
# Constraints: --self-test requires --test-db; every production command still requires an exact database name and write switch.
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
