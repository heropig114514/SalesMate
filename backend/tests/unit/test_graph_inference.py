"""职责：验证推理配置不丢失业务上下文，并在HTTP边界保留明确失败语义。
实现：用完整正式schema和模拟本机HTTP响应检查提示、缓存、预算与审计；不启动真实模型。
关联：inference_profiles、semantic_provider、semantic_contract；真实性能由独立基准测量。
目录：
- GraphInferenceTests：配置和调用契约测试。
- GraphInferenceTests.setUp：构造完整schema及动态上下文。
- GraphInferenceTests.test_schema_first_preserves_all_content：检查键重排不改变任何数据。
- GraphInferenceTests.test_prefix_stays_stable_before_dynamic_time：检查不同时间仍共享schema前缀。
- GraphInferenceTests.test_invalid_profile_and_envelope_fail：拒绝未知配置和缺字段输入。
- GraphInferenceTests.test_explicit_server_configuration：核验上下文与缓存参数互相一致。
- GraphInferenceTests.test_provider_payload_and_audit：检查真实发送体、超时与审计的一致性。
- GraphInferenceTests.test_provider_failure_is_not_retried：模型超时仅调用一次且向上传播。
- GraphInferenceTests.test_unknown_profile_does_not_call_http：配置错误发生于网络调用前。
变量索引：
- 无
"""
import copy
import hashlib
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from apps.knowledge_graph.business_schema import catalog
from apps.knowledge_graph.inference_profiles import PROFILES, get_profile, prepare_messages, server_arguments
from apps.knowledge_graph.semantic_contract import messages
from apps.knowledge_graph.semantic_provider import generate


# 功能：验证优化边界及明确失败行为。
# 逻辑：正式schema与函数参与验证；仅HTTP返回值被模拟。
# 约束：不能由这些测试推断真实Qwen质量、缓存命中或服务器速度。
class GraphInferenceTests(SimpleTestCase):
    # 功能：构造动态字段齐全的消息。
    # 输入：无外部参数；读取正式业务目录。
    # 输出：prompt实例状态，包含全部schema和非空候选/事实。
    # 逻辑：动态值用于发现重排时的丢字段或副作用。
    # 约束：候选是纯合成字典，不访问数据库。
    def setUp(self):
        self.prompt = messages("新邮件：Acme needs Edge.", "2026-09-25T01:00:00+00:00", {
            "schema": catalog(), "entities": [{"id": "synthetic-1", "name": "Acme"}],
            "facts": [{"subject": "synthetic-1", "predicate": "name", "value": "Acme"}], "generation": 7,
        })

    # 功能：证明完整schema和动态值被原样保留。
    # 输入：无外部参数；使用prompt及每项声明配置。
    # 输出：消息JSON语义相等且源消息未发生修改。
    # 逻辑：独立深复制对照，不以函数自身构建预期结果。
    # 约束：仅允许user JSON键顺序改变，不允许改变system信任边界。
    def test_schema_first_preserves_all_content(self):
        original = copy.deepcopy(self.prompt)
        for name in PROFILES:
            result = prepare_messages(self.prompt, get_profile(name))
            self.assertEqual(result[0], original[0])
            self.assertEqual(json.loads(result[1]["content"]), json.loads(original[1]["content"]))
            self.assertEqual(self.prompt, original)
            self.assertIsNot(result, self.prompt)
            if name == "baseline":
                self.assertEqual(result, original)

    # 功能：验证稳定schema前缀位于变化时间之前。
    # 输入：无外部参数；仅改变同一消息的observed_at。
    # 输出：时间之前的序列化前缀相同，完整目录仍存在。
    # 逻辑：检查首字段和前缀边界，不把字符前缀等同真实token缓存命中。
    # 约束：实际cache_n由真实基准响应验证。
    def test_prefix_stays_stable_before_dynamic_time(self):
        changed = copy.deepcopy(self.prompt)
        body = json.loads(changed[1]["content"])
        body["observed_at"] = "2026-09-26T01:00:00+00:00"
        changed[1]["content"] = json.dumps(body, ensure_ascii=False)
        first, second = [prepare_messages(prompt, get_profile("prefix16"))[1]["content"] for prompt in (self.prompt, changed)]
        self.assertTrue(first.startswith('{"existing_context": {"schema":'))
        self.assertEqual(first.split('"observed_at":')[0], second.split('"observed_at":')[0])
        self.assertNotEqual(first, second)

    # 功能：未知配置和不完整信封必须明确拒绝。
    # 输入：无外部参数；模拟调用方协议不匹配。
    # 输出：ValueError；已取配置的修改不污染后续读取。
    # 逻辑：覆盖未知名称、缺失generation和可变配置副作用。
    # 约束：不添加备用配置或隐式补默认字段。
    def test_invalid_profile_and_envelope_fail(self):
        with self.assertRaises(ValueError):
            get_profile("automatic")
        profile = get_profile("prefix16")
        profile["context"] = 1
        self.assertEqual(get_profile("prefix16")["context"], 16384)
        broken = copy.deepcopy(self.prompt)
        body = json.loads(broken[1]["content"])
        del body["existing_context"]["generation"]
        broken[1]["content"] = json.dumps(body)
        with self.assertRaises(ValueError):
            prepare_messages(broken, profile)

    # 功能：确保模型启动参数不允许静默缩减或滑动。
    # 输入：无外部参数；遍历配置，模拟合法/非法线程数。
    # 输出：单槽位、明确上下文/KV类型、禁用fit/滑动及缓存开关断言。
    # 逻辑：检查CLI参数位置及互斥项。
    # 约束：不能替代真实二进制对参数的支持验证。
    def test_explicit_server_configuration(self):
        for name, profile in PROFILES.items():
            args = server_arguments(name, 2)
            self.assertEqual(args[args.index("-c") + 1], str(profile["context"]))
            self.assertEqual(args[args.index("-ctv") + 1], profile["kv"])
            self.assertEqual(args[args.index("--fit") + 1], "off")
            self.assertIn("--no-context-shift", args)
            self.assertEqual("--cache-prompt" in args, profile["cache"])
            self.assertEqual("--no-cache-prompt" in args, not profile["cache"])
        for threads in (0, -1, True):
            with self.assertRaises(ValueError):
                server_arguments("baseline", threads)

    # 功能：核验模型实际请求和审计记录一致。
    # 输入：无外部参数；模拟成功HTTP，覆盖默认、首轮缓存及补充prefix8配置。
    # 输出：缓存、消息、哈希、预算、超时和代理行为断言。
    # 逻辑：通过requests.Session边界检查实际发送内容，保持输入不变。
    # 约束：模拟缓存计数仅验证转存，不证明真实缓存加速。
    def test_provider_payload_and_audit(self):
        for name in ("baseline", "prefix16", "combined8", "prefix8"):
            env = {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph"}
            if name != "baseline":
                env["GRAPH_LLM_PROFILE"] = name
            with patch.dict("os.environ", env, clear=True), patch("requests.Session") as factory:
                session = factory.return_value.__enter__.return_value
                session.post.return_value.status_code = 200
                session.post.return_value.json.return_value = {"model": "salesmate-graph", "timings": {"cache_n": 123},
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"entities": [], "facts": []}'}}]}
                parsed, audit = generate(self.prompt)
                call = session.post.call_args
                self.assertEqual(call.kwargs["timeout"], (10, 300))
                self.assertFalse(call.kwargs["allow_redirects"])
                self.assertFalse(session.trust_env)
                payload = call.kwargs["json"]
                self.assertEqual(payload["cache_prompt"], get_profile(name)["cache"])
                self.assertEqual((payload["temperature"], payload["seed"], payload["max_tokens"]), (0, 2026, 1536))
                self.assertEqual(json.loads(payload["messages"][1]["content"]), json.loads(self.prompt[1]["content"]))
                expected = hashlib.sha256(json.dumps(payload["messages"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                self.assertEqual(audit["prompt_sha256"], expected)
                self.assertEqual(audit["inference_profile"], name)
                self.assertEqual(audit["timings"], {"cache_n": 123})
                self.assertEqual(parsed, {"entities": [], "facts": []})

    # 功能：防止在模型超时后自动重试或切换配置。
    # 输入：无外部参数；HTTP边界模拟ReadTimeout。
    # 输出：同一异常向上传播且post仅调用一次。
    # 逻辑：验证失败语义不被优化路径改变。
    # 约束：不等待真实300秒，也不声称网络超时已实测。
    def test_provider_failure_is_not_retried(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph",
                                     "GRAPH_LLM_PROFILE": "combined8"}), patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.ReadTimeout("synthetic timeout")
            with self.assertRaises(requests.ReadTimeout):
                generate(self.prompt)
            session.post.assert_called_once()

    # 功能：避免未知配置静默使用默认模型调用。
    # 输入：无外部参数；显式错误GRAPH_LLM_PROFILE。
    # 输出：ValueError且无Session创建。
    # 逻辑：在网络副作用前拒绝配置。
    # 约束：后端统一错误包装由既有集成测试覆盖。
    def test_unknown_profile_does_not_call_http(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph",
                                     "GRAPH_LLM_PROFILE": "typo"}), patch("requests.Session") as factory:
            with self.assertRaises(ValueError):
                generate(self.prompt)
            factory.assert_not_called()
