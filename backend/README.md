# SalesMate 软件应用

本目录统一维护前后端软件、测试、契约、文档与开发工具。下文命令和相对路径以 `SalesMate/backend/` 为基准；仓库概览见[根目录说明](../README.md)。后续独立 Agent 位于同级 `agent/`。

面向销售人员的邮件理解与客户跟进系统：整理 Gmail 往来、关联公司和联系人、生成客户画像与分析，并辅助确定跟进优先级。

当前已实现前后端联调闭环：原生 HTML/CSS/JavaScript 工作台、Django + DRF、PostgreSQL、邮件归组、版本化事实、Job Pull、分析与评分接口。暂用显式规则占位，后续可切换独立 Agent。数据库迁移已执行；真实 Gmail OAuth 与模型尚未接入。

本地入口：[客户工作台](http://127.0.0.1:8000/)。本地调试默认跳过登录页，以已有普通账号 demo 进入，可导入演示样例、查看详情与原文依据、建档并模拟来信。在 `.env` 设置 `LOCAL_DEBUG_AUTO_LOGIN=False` 并重启可恢复登录；账号信息保存在被 Git 排除的 `.local-access.json`。

## Coding Agent 必须遵循的开发原则

所有 Coding Agent 在新增、修改、重构或删除本软件目录内代码（包括前端、测试与工具）时，必须遵循以下要求：

1. **学术风格的实现注释**：在函数、方法及关键代码块处提供准确、严谨、可核验的注释，说明功能、输入与输出、实现逻辑、设计依据和适用约束；涉及状态转换、边界条件、异常或副作用时，应说明其处理方式。注释应解释实现原因与逻辑关系，避免仅复述代码，也不得编造学术引用或未经验证的结论。
2. **文件顶部的功能说明与目录**：每个代码文件顶部必须说明文件职责、主要实现逻辑及与相关模块的关系，并列出文件中实际实现的函数、类与关键方法，以及关键变量、常量和配置项的名称与用途，供 Coding Agent 和开发者快速定位。目录应与当前实现一致，不保留已删除或重命名的条目。
3. **代码与注释同步原子修改**：代码实现、对应注释和文件顶部目录必须作为同一逻辑变更单元同步更新、检查和交付；如提交代码，必须纳入同一次提交。修改函数签名、行为、数据流、关键变量或模块职责时，必须同时修订受影响的说明；删除或替换实现时，必须同步清理失效注释及目录引用。不得先交付代码，再以“后续补充”为由延迟更新注释。
4. **完成前检查一致性**：交付前逐项核对变更涉及的注释和目录，确认其准确反映实际行为，并在软件根目录（`SalesMate/backend/`）运行 `python tools/check_docs.py` 检查声明注释、目录及模块变量索引。修改检查器时还须运行 `python tools/test_check_docs.py`。自动检查不能替代对功能说明、实现逻辑、关键状态与代码一致性的人工核对，也不能证明 Git 提交原子性。

检查工具已提供：在软件根目录运行上述命令。默认扫描本目录下全部 `.py` 文件，包括 `tools/`，包含测试、迁移和包初始化文件。检查失败返回非零退出码，并报告文件、行号与具体问题。统一注释格式、索引范围、检查能力及人工核对要求见[代码注释与一致性检查规范](docs/coding-agent-guidelines.md)。

## 项目文档

- [代码注释与一致性检查规范](docs/coding-agent-guidelines.md)：Coding Agent 的说明格式、符号索引、检查命令与交付要求。
- [项目参考总览](docs/project-reference.md)：Google 产品文档、Agent 设计与早期技术方案的本地归纳，包含场景、职责、当前进度与待确认事项。
- [架构与技术选型（暂定）](docs/architecture-and-tech-stack.md)：Django 后端、Agent 协作方式、数据存储、RAG 扩展及分阶段引入计划。
- [项目目录与文件规划](docs/project-structure.md)：前后端与 Agent 的目录边界、Django 模块结构、文件职责和实施顺序。
- [本地开发环境与验证状态](docs/local-development.md)：D 盘 Conda 环境、已验证组件，以及先本地开发、后容器化的安排。
- [当前 API 契约](docs/api-contract.md)：README 通信对象的 HTTP 映射、业务查询、服务认证、版本、租约与错误语义。
- [Agent 替换与联调](docs/agent-integration.md)：规则占位范围、版本和切换独立 Agent 的步骤。
- [数据模型现状](docs/data-model.md)：邮件、公司、抽取版本、快照、分析、评分与任务的真实数据库结构。
- [产品功能文档：SalesMate MVP（0909 更新）](https://docs.google.com/document/d/1IG0NtzszF1-RVtFgIuH_4KKTUuhKrehARNn_H6gt3aQ/edit)：页面与用户需求，访问需要相应权限。

## 当前技术方向

业务后端采用 Django + Django REST Framework，业务数据使用 PostgreSQL；Agent 作为独立 Python 服务，通过后端 API 领取任务、读取上下文并提交分析结果。

RAG 拟采用 pgvector，复杂助手流程拟采用 LangGraph。文档处理、对象存储、Celery + Redis 和 Langfuse 按对应功能需要分阶段引入，具体范围见技术选型文档。

当前使用本地 Conda 环境和 WSL PostgreSQL 16，前端与后端同源，无需 Node 构建。`ANALYSIS_PROVIDER=rules` 明确使用占位；改为 `agent` 并重启后，任务由独立 Agent 消费。pgvector、Docker 和复杂助手留待相应范围确认。

## 本地启动

先按[开发环境说明](docs/local-development.md)安装依赖并配置 `.env`，然后在本目录执行：

```powershell
conda activate django_env
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

- [API 文档](http://127.0.0.1:8000/api/docs/)
- [服务存活检查](http://127.0.0.1:8000/api/v1/health/live/)
- [数据库就绪检查](http://127.0.0.1:8000/api/v1/health/ready/)

本机已完成 PostgreSQL 与迁移。数据库停止时就绪接口仍明确返回 `503`；不会回退到 SQLite。WSL 保活、本地账号及其他机器初始化方式见开发环境说明。

在本目录执行 `python manage.py test tests`，包含真实 PostgreSQL 业务和并发测试，以及原九项框架测试。原框架测试的数据库就绪分支仍使用显式模拟，不能替代真实集成验证。前端浏览器验证脚本为 `tools/browser_smoke.cjs`。
