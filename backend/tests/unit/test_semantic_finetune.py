"""职责：验证合成微调语料与评价程序的关键约束。
实现：使用正式schema/校验器，检查标签、局部键不变性、错误语义及身份解析归因。
关联：tools.build_semantic_corpus 与 tools.semantic_finetune_kernel；模型及Kaggle不在单元测试内模拟为成功。
目录：
- SemanticFinetuneTests：独立语料与评价契约测试。
- SemanticFinetuneTests.setUp：建立受控schema及校验函数。
- SemanticFinetuneTests.test_all_gold_cases_validate：所有场景标签可校验。
- SemanticFinetuneTests.test_key_names_do_not_change_score：局部键重命名不改变语义评分。
- SemanticFinetuneTests.test_false_employment_is_counted：联系人误判任职会扣分。
- SemanticFinetuneTests.test_invalid_evidence_fails：伪造原文不能计为正确。
- SemanticFinetuneTests.test_resolver_credit_is_separate：模型未对齐与程序对齐分别计分。
变量索引：
- 无
"""
import copy
from django.test import SimpleTestCase
from apps.knowledge_graph.business_schema import catalog
from apps.knowledge_graph.semantic_contract import validate_extraction
from apps.knowledge_graph.entity_resolution import resolve_entities
from tools.build_semantic_corpus import example, FAMILIES
from tools.semantic_finetune_kernel import score


# 功能：核验微调语料和评估契约。
# 逻辑：使用真实函数与合成字典，不访问数据库或模型。
# 约束：测试通过不证明训练收敛、量化质量或真实邮件准确率。
class SemanticFinetuneTests(SimpleTestCase):
    # 功能：准备业务schema及正式校验函数。
    # 输入：无外部参数，读取已初始化Django模型元数据。
    # 输出：schema和contract实例状态。
    # 逻辑：不复制或简化实际校验逻辑。
    # 约束：没有业务行查询。
    def setUp(self):
        self.schema = catalog()
        self.contract = {"validate_extraction": validate_extraction, "resolve_entities": resolve_entities}

    # 功能：验证三种划分中所有场景标签。
    # 输入：无外部参数；读取12类固定生成器。
    # 输出：全部标签及程序解析均正确，划分实体名称不重叠。
    # 逻辑：逐场景执行与正式评估相同函数。
    # 约束：模板共享已知，不能声称措辞或领域分布独立。
    def test_all_gold_cases_validate(self):
        identities = []
        for split in ("train", "dev", "test"):
            names = set()
            for family in FAMILIES:
                row = example(split, 0, family, self.schema)
                metrics = score(row["gold"], row, self.contract)
                self.assertTrue(metrics["valid"] and metrics["facts_exact"] and metrics["resolved_identity_exact"])
                names.update(item["name"] for item in row["gold"]["entities"])
            identities.append(names)
        self.assertFalse(identities[0] & identities[1] or identities[0] & identities[2])

    # 功能：验证评分不依赖任意局部编号。
    # 输入：无外部参数；同一事实仅重命名e1。
    # 输出：事实与身份精确匹配保持成立。
    # 逻辑：端点按实体类型和名称规范化，不按数组顺序。
    # 约束：不放宽名称、数值或引句证据要求。
    def test_key_names_do_not_change_score(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["entities"][0]["key"] = "e9"
        for item in result["facts"]:
            item["subject"] = "e9"
        self.assertTrue(score(result, row, self.contract)["facts_exact"])

    # 功能：验证原文存在但语义过强的任职关系会被金标准扣分。
    # 输入：无外部参数；把联系人field:company改为works_for。
    # 输出：证据结构仍有效，但产生1个误检和1个漏检。
    # 逻辑：区分协议合法性与语义正确性。
    # 约束：不把原文校验等同人工确认。
    def test_false_employment_is_counted(self):
        row = example("test", 0, "contact_not_employee", self.schema)
        result = copy.deepcopy(row["gold"])
        result["facts"][0]["predicate"] = "works_for"
        metrics = score(result, row, self.contract)
        self.assertTrue(metrics["valid"])
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"]), (0, 1, 1))

    # 功能：拒绝不在输入中的证据。
    # 输入：无外部参数；属性引句被替换为虚构文本。
    # 输出：整题valid和facts_exact均False。
    # 逻辑：调用正式校验，不修复输出后评分。
    # 约束：失败样本仍须保留在汇总分母。
    def test_invalid_evidence_fails(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["facts"][0]["quote"] = "invented source"
        metrics = score(result, row, self.contract)
        self.assertFalse(metrics["valid"] or metrics["facts_exact"])

    # 功能：分别评价模型与程序的身份关联贡献。
    # 输入：无外部参数；模型遗漏一个唯一已知实体ID。
    # 输出：原始身份匹配失败，解析后通过，原始字典不修改。
    # 逻辑：模拟真实冒烟遇到的ID遗漏，不模拟训练成功。
    # 约束：不能将解析后的指标描述为模型独立能力。
    def test_resolver_credit_is_separate(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["entities"][0]["existing_id"] = None
        metrics = score(result, row, self.contract)
        self.assertFalse(metrics["raw_identity_exact"])
        self.assertTrue(metrics["resolved_identity_exact"])
        self.assertIsNone(result["entities"][0]["existing_id"])
