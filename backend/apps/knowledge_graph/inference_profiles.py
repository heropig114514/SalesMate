"""职责：声明语义建图推理优化的显式配置与可缓存提示词排列。
实现：基线参数保持不变；其他配置逐项改变上下文、注意力、KV精度或共同前缀排列。
关联：run_graph_model、semantic_provider及本地对照程序共用；不改训练数据或事实校验。
目录：
- get_profile：验证显式配置名并返回独立配置。
- prepare_messages：保持完整内容，仅将稳定schema放到动态时间之前。
- server_arguments：生成固定模型配置参数。
变量索引：
- PROFILES：首轮六项及补充prefix8部署对照配置，baseline是唯一默认值。
"""
import copy
import json

PROFILES = {
    "baseline": {"context": 16384, "kv": "f16", "flash": "auto", "cache": False, "prompt_layout": "legacy"},
    "context8": {"context": 8192, "kv": "f16", "flash": "auto", "cache": False, "prompt_layout": "legacy"},
    "flash8": {"context": 8192, "kv": "f16", "flash": "on", "cache": False, "prompt_layout": "legacy"},
    "kv8": {"context": 8192, "kv": "q8_0", "flash": "on", "cache": False, "prompt_layout": "legacy"},
    "prefix16": {"context": 16384, "kv": "f16", "flash": "auto", "cache": True, "prompt_layout": "schema-first-v1"},
    "combined8": {"context": 8192, "kv": "q8_0", "flash": "on", "cache": True, "prompt_layout": "schema-first-v1"},
    "prefix8": {"context": 8192, "kv": "f16", "flash": "auto", "cache": True, "prompt_layout": "schema-first-v1"},
}


# 功能：读取明确命名的推理配置。
# 输入：`name` 为PROFILES中的精确名称。
# 输出：独立配置字典；未知名称抛ValueError。
# 逻辑：深复制以防调用方污染其他请求和固定实验配置。
# 约束：不选择备用配置，也不自动调整参数。
def get_profile(name):
    if name not in PROFILES:
        raise ValueError("Unknown graph inference profile")
    return copy.deepcopy(PROFILES[name])


# 功能：准备完整且可复用共同前缀的消息。
# 输入：`prompt` 为既有system/user消息；`profile`为已验证配置字典。
# 输出：深复制消息，schema-first配置仅重新排列JSON键。
# 逻辑：保留每个schema、候选、事实、时间及新文本；稳定schema在候选和时间之前。
# 约束：不截断、不筛选、不将已有事实当新证据；原协议形状不符时明确失败。
def prepare_messages(prompt, profile):
    result = copy.deepcopy(prompt)
    if profile["prompt_layout"] == "legacy":
        return result
    if len(result) != 2 or result[0]["role"] != "system" or result[1]["role"] != "user":
        raise ValueError("Unexpected semantic message envelope")
    content = json.loads(result[1]["content"])
    if set(content) != {"observed_at", "existing_context", "new_text"}:
        raise ValueError("Unexpected semantic context envelope")
    context = content["existing_context"]
    if set(context) != {"schema", "entities", "facts", "generation"}:
        raise ValueError("Unexpected semantic context fields")
    ordered = {"existing_context": {"schema": context["schema"], "entities": context["entities"],
               "facts": context["facts"], "generation": context["generation"]},
               "observed_at": content["observed_at"], "new_text": content["new_text"]}
    result[1]["content"] = json.dumps(ordered, ensure_ascii=False)
    return result


# 功能：构造单槽位CPU模型的配置参数。
# 输入：`name` 为已声明配置名；`threads`为正整数CPU线程数。
# 输出：传给llama-server的参数列表。
# 逻辑：上下文、KV类型、Flash Attention及缓存按配置显式设置；保留无fit与无滑动。
# 约束：默认baseline不增加低精度KV；缓存只复用实际相同token前缀，不拼入旧请求。
def server_arguments(name, threads):
    profile = get_profile(name)
    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be a positive integer")
    return ["-ngl", "0", "-t", str(threads), "-tb", str(threads), "-c", str(profile["context"]),
            "-np", "1", "--fit", "off", "-fa", profile["flash"], "-ctk", profile["kv"], "-ctv", profile["kv"],
            "--cache-ram", "0", "--cache-prompt" if profile["cache"] else "--no-cache-prompt",
            "--no-context-shift", "--offline"]
