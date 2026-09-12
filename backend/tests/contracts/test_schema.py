"""职责：验证当前生成的 OpenAPI 与版本化契约一致。
实现：生成并校验 Schema 后比较解析后的 YAML 结构，避免仅比较格式或文本排版。
关联：读取软件根目录内 contracts/openapi.yaml，使用 Django 配置和 drf-spectacular；不调用真实业务数据库。

目录：
- SchemaTests：检查生成的 API 契约与已保存版本一致。
- SchemaTests.test_generated_schema_matches_versioned_contract：验证 Schema 合法性和版本化契约一致性。

变量索引：
- 无
"""

import yaml
from django.conf import settings
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema


# 功能：检查生成的 API 契约与已保存版本一致。
# 逻辑：继承 SimpleTestCase 并读取项目契约文件执行结构比较。
# 约束：不创建数据库；依赖当前 Django 配置与安装的 Schema 工具版本。
class SchemaTests(SimpleTestCase):
    # 功能：验证 Schema 合法性和版本化契约一致性。
    # 输入：无外部参数；读取 settings.BASE_DIR 软件根目录内的契约文件。
    # 输出：返回 None；校验失败、文件不可读或结构不等时测试失败。
    # 逻辑：公开生成完整 Schema，执行规范校验，然后与 safe_load 后的 YAML 比较。
    # 约束：不修改契约文件；通过只证明当前生成结构一致，不证明业务接口已联调。
    def test_generated_schema_matches_versioned_contract(self):
        generated = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(generated)
        contract = settings.BASE_DIR / "contracts" / "openapi.yaml"
        self.assertEqual(yaml.safe_load(contract.read_text(encoding="utf-8")), generated)
