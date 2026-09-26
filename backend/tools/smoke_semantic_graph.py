"""Responsibility: Run a real local-model automatic graph-building smoke test in an explicitly named isolated database.
Implementation: Creates synthetic customers and products, partial quotation observations, and sequentially submits two Chinese texts; saves every result, then validates links between existing and new entities and budget candidates.
Relationships: Reuses production episodes services and the already-started local model; test records remain in the separate database and must not target the business database.
Directory:
- main: Create the synthetic scenario and validate one real flow.
Variable index:
- None
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid


# Function: Run an auditable local-model smoke test.
# Inputs: No function parameters; CLI database must begin with salesmate_semantic_smoke_ and output must be a nonexistent result directory.
# Outputs: Result for each input and verification.json; failure writes failure.json, including invalid model candidates for synthetic input, and exits nonzero.
# Logic: Migrates the specified isolated database first and uses a dedicated synthetic account; calls the model exactly twice, without modifying output or retrying automatically.
# Constraints: The smoke test does not represent general extraction accuracy or a performance benchmark; it does not connect to or modify the original business database and sends no remote requests.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.database.startswith("salesmate_semantic_smoke_"):
        raise ValueError("Only a dedicated semantic smoke database is allowed")
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections
    connections.close_all()
    settings.DATABASES["default"]["NAME"] = args.database
    connections["default"].settings_dict["NAME"] = args.database
    from django.core.management import call_command
    from django.core.serializers.json import DjangoJSONEncoder
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from apps.crm.models import Company
    from apps.sales.models import Product
    from apps.knowledge_graph.episodes import ingest, episode_data
    from apps.knowledge_graph.models import Entity, Fact
    from apps.knowledge_graph.projection import identity
    try:
        call_command("migrate", interactive=False, verbosity=0)
        owner = get_user_model().objects.create_user(username="semantic-smoke-" + uuid.uuid4().hex[:10])
        company = Company.objects.create(owner=owner, group_key="acme.example", name="Acme")
        Product.objects.create(owner=owner, sku="EDGE", name="Edge", currency="SGD", unit_price="100")
        observed = timezone.now()
        inputs = [
            {"source_key": "partial-quote", "records": [{"key": "quote", "schema": "sales.quote", "fields": {"number": "Q-DEMO", "company": str(company.pk)}}]},
            {"source_key": "meeting-one", "text": "Mira 是 Acme 的联系人。Acme 需要 Edge，预算为 800 SGD。"},
            {"source_key": "meeting-two", "text": "Mira 是 Acme 的采购决策人。Acme 的预算调整为 1200 SGD。"},
        ]
        for index, payload in enumerate(inputs):
            print(f"INPUT {index + 1} source={payload['source_key']}", flush=True)
            result = episode_data(ingest(owner.pk, observed_at=observed, **payload))
            (args.output / f"episode-{index + 1}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder), encoding="utf-8")
            print(f"SAVED {index + 1} episode={result['id']}", flush=True)
        company_id = identity("entity", owner.pk, "crm.company", str(company.pk))
        budgets = list(Fact.objects.filter(owner=owner, subject_id=company_id, predicate="reported_budget").exclude(status="unsupported").values("value", "status"))
        contacts = Entity.objects.filter(owner=owner, kind="external.crm.contact", active=True)
        facts = list(Fact.objects.filter(owner=owner, origin="extraction").exclude(status="unsupported").values("subject_id", "predicate", "object_id", "value", "status"))
        assert contacts.count() == 1, "The second input did not reuse the first contact"
        assert {row["value"] for row in budgets} == {"800 SGD", "1200 SGD"}, "Budget extraction differs from the declared smoke expectation"
        assert all(row["status"] == "needs_review" for row in budgets)
        assert not Entity.objects.filter(owner=owner, kind="external.crm.company", active=True).exists(), "The model duplicated the known company"
        verification = {"passed": True, "database": args.database, "owner_id": owner.pk, "real_model_calls": 2, "inputs": len(inputs),
                        "external_contacts": contacts.count(), "budgets": budgets, "facts": facts,
                        "limitations": ["Synthetic smoke only", "No general accuracy claim", "Separate observations preserve conflicting budgets"]}
        (args.output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder), encoding="utf-8")
        print("PASS real local model: partial schema + new entity + later association + conflicting observations", flush=True)
    except Exception as exc:
        (args.output / "failure.json").write_text(json.dumps({"type": type(exc).__name__, "message": str(exc),
                "candidate": getattr(exc, "graph_candidate", None), "model_audit": getattr(exc, "graph_model_audit", None)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
