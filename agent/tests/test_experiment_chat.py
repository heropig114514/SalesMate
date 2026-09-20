"""职责：验证新实验来源在原有聊天预算内可被正确展示。
实现：构造超过来源数量上限的分页证据，并校验文件可见性与分页参数边界。
关联：workflows.chat 的实验工具投影；真实权限及 HTTP 在后端集成测试覆盖。
目录：
- ExperimentChatTests：纯工作流实验边界测试。
- ExperimentChatTests.test_file_evidence_survives_full_page：文件证据不会被此前整页行记录挤出。
- ExperimentChatTests.test_rows_keep_existing_page_budget：实验行沿用既定分页预算且不允许写入。
变量索引：
- 无
"""

import unittest

from agent.workflows.chat import ChatValidationError, _workspace_arguments, _workspace_prompt_evidence


# 功能：验证新增实验工具的展示及参数约束。
# 逻辑：只测试模型输入预算和工具选择，不模拟成功授权。
# 约束：无网络、数据库和真实模型调用。
class ExperimentChatTests(unittest.TestCase):
    # 功能：确保最新读取的文件仍能作为可见引用来源。
    # 输入：二十条行记录加一个随后读取的文件来源。
    # 输出：文件属于完整白名单及展示片段，来源数不超过原上限 12。
    # 逻辑：通过实际来源选择函数排序，不改变预算来获得通过。
    # 约束：只验证展示机制，不证明文件内容真实或用户具备读取权限。
    def test_file_evidence_survives_full_page(self):
        rows = [{"source_id": str(index), "source_type": "experiment_row", "title_or_label": "虚构行",
                 "content": "虚构记录"} for index in range(20)]
        file = {"source_id": "file", "source_type": "experiment_file", "title_or_label": "虚构附件", "content": "虚构正文"}
        visible, prompt = _workspace_prompt_evidence(rows + [file], "读取实验附件")
        self.assertEqual(len(visible), 12)
        self.assertEqual(visible[0], file)
        self.assertEqual(prompt[0], file)

    # 功能：确保新增分页工具使用原有预算。
    # 输入：缺省页大小、超过预算和写工具名称。
    # 输出：缺省为 20，超限及写工具抛出 ChatValidationError。
    # 逻辑：直接检查模型动作参数入口，后端仍另行检查完整 Schema。
    # 约束：不改变原字典，不把客户端验证当作授权。
    def test_rows_keep_existing_page_budget(self):
        args = {"batch": "KGSEED_20260921_01", "model": "crm.Company"}
        _, parsed = _workspace_arguments("experiments.rows", args)
        self.assertEqual(parsed["page_size"], 20)
        self.assertNotIn("page_size", args)
        for name, values in (("experiments.rows", {**args, "page_size": 21}), ("customers.create", {})):
            with self.assertRaises(ChatValidationError):
                _workspace_arguments(name, values)
