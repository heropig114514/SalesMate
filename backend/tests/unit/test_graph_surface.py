"""Responsibility: Verify that retired research modules no longer remain exposed as routes or tools while current graph entry points are retained.
Implementation: Reads real Django URL configuration and the dynamic tool catalog; does not connect to databases, models, or external platforms.
Relationships: knowledge_graph.urls, sales.urls, agent_tools.registry, and permission-mode settings.
Directory:
- GraphSurfaceTests: Regression tests for retiring the public graph surface.
- GraphSurfaceTests.test_catalog_preserves_graph_in_both_modes: Verify tool sets in production and laboratory modes.
- GraphSurfaceTests.test_routes_preserve_graph_and_remove_research: Verify current routes resolve and retired routes do not exist.
Variable index:
- None
"""
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve
from apps.agent_tools.registry import build_registry


# Function: Check the public call surface after module removal.
# Logic: Uses the real registry and route resolution rather than relying only on source-string searches.
# Constraints: Does not verify authorization execution, database writes, or model generation; existing integration tests cover these.
class GraphSurfaceTests(SimpleTestCase):
    # Function: Check that the tool catalog publishes only retained graph capabilities.
    # Inputs: No external parameters; explicitly iterates the laboratory-access switch and disables personal-workspace override to cover both modes.
    # Outputs: Assertions that nine graph tools exist and retired tools are absent.
    # Logic: The real business serializer constructs the registry; the catalog is not faked.
    # Constraints: override_settings restores settings and does not create or broaden real credentials.
    def test_catalog_preserves_graph_in_both_modes(self):
        expected = {"graph.schema", "graph.status", "graph.entities", "graph.facts", "graph.lineage",
                    "graph.episodes", "graph.episode", "graph.ingest", "graph.retract"}
        for enabled in (False, True):
            with self.subTest(lab=enabled), override_settings(LAB_OPEN_ACCESS=enabled, WORKSPACE_OWNER_ONLY=False):
                names = set(build_registry())
                self.assertEqual({name for name in names if name.startswith("graph.")}, expected)
                self.assertFalse(any(name.startswith(("crmarena.", "ease.")) for name in names))

    # Function: Check that current graph routes remain routable and retired public research and recommendation endpoints have no match.
    # Inputs: No external parameters; current schema/input/status paths and the retired-path list.
    # Outputs: Correct current view ownership and Resolver404 for retired URLs.
    # Logic: Resolves the real root URL configuration, covering include mounts rather than only child modules.
    # Constraints: Successful resolution does not establish service readiness and sends no HTTP or model request.
    def test_routes_preserve_graph_and_remove_research(self):
        for path in ("schema/", "episodes/", "status/", "entities/", "facts/"):
            match = resolve("/api/v1/graph/" + path)
            self.assertTrue(match.func.view_class.__module__.startswith("apps.knowledge_graph."))
        for path in ("/api/v1/graph/crmarena/", "/api/v1/graph/crmarena/evidence/",
                     "/api/v1/graph/crmarena/evaluation/", "/api/v1/graph/crmarena/predict/",
                     "/api/v1/sales/recommendations/ease/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
