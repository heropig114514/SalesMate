"""职责：验证退役研究模块不会继续暴露为路由或工具，并保留当前图谱入口。
实现：读取真实 Django URL 配置与动态工具目录；不连接数据库、模型或外部平台。
关联：knowledge_graph.urls、sales.urls、agent_tools.registry 与权限模式设置。
目录：
- GraphSurfaceTests：图谱公开接口退役回归。
- GraphSurfaceTests.test_catalog_preserves_graph_in_both_modes：核验正式和实验模式的工具集合。
- GraphSurfaceTests.test_routes_preserve_graph_and_remove_research：核验当前路由可解析、退役路由不存在。
变量索引：
- 无
"""
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve
from apps.agent_tools.registry import build_registry


# 功能：检查模块删除后的公开调用面。
# 逻辑：使用真实注册表与路由解析，避免仅依赖源码字符串搜索。
# 约束：不验证权限执行、数据库写入或模型生成；这些由既有集成测试覆盖。
class GraphSurfaceTests(SimpleTestCase):
    # 功能：检查工具目录只发布保留的图谱能力。
    # 输入：无外部参数；显式遍历实验访问开关，关闭个人空间覆盖以覆盖两种模式。
    # 输出：九项图谱工具存在、退役工具缺失的断言。
    # 逻辑：注册表由真实业务序列化器构造，不伪造目录。
    # 约束：恢复设置由override_settings完成，不创建或扩大真实凭证。
    def test_catalog_preserves_graph_in_both_modes(self):
        expected = {"graph.schema", "graph.status", "graph.entities", "graph.facts", "graph.lineage",
                    "graph.episodes", "graph.episode", "graph.ingest", "graph.retract"}
        for enabled in (False, True):
            with self.subTest(lab=enabled), override_settings(LAB_OPEN_ACCESS=enabled, WORKSPACE_OWNER_ONLY=False):
                names = set(build_registry())
                self.assertEqual({name for name in names if name.startswith("graph.")}, expected)
                self.assertFalse(any(name.startswith(("crmarena.", "ease.")) for name in names))

    # 功能：检查当前图谱仍可路由，已退役的公开研究和推荐接口返回无匹配。
    # 输入：无外部参数；当前schema/输入/状态路径与退役路径清单。
    # 输出：当前视图归属正确、退役URL抛Resolver404。
    # 逻辑：解析真实根URL配置，覆盖include挂载而非只检查子模块。
    # 约束：解析成功不代表服务就绪，不发送HTTP或模型请求。
    def test_routes_preserve_graph_and_remove_research(self):
        for path in ("schema/", "episodes/", "status/", "entities/", "facts/"):
            match = resolve("/api/v1/graph/" + path)
            self.assertTrue(match.func.view_class.__module__.startswith("apps.knowledge_graph."))
        for path in ("/api/v1/graph/crmarena/", "/api/v1/graph/crmarena/evidence/",
                     "/api/v1/graph/crmarena/evaluation/", "/api/v1/graph/crmarena/predict/",
                     "/api/v1/sales/recommendations/ease/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
