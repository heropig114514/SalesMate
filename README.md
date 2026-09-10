# SalesMate

面向销售人员的邮件理解与客户跟进系统：整理 Gmail 往来、关联公司和联系人、生成客户画像与分析，并辅助确定跟进优先级。

当前仓库处于方案整理阶段；文档中的技术选型与功能规划不代表已经实现。

## 项目文档

- [架构与技术选型（暂定）](docs/architecture-and-tech-stack.md)：Django 后端、Agent 协作方式、数据存储、RAG 扩展及分阶段引入计划。
- [产品功能文档：SalesMate MVP（0909 更新）](https://docs.google.com/document/d/1IG0NtzszF1-RVtFgIuH_4KKTUuhKrehARNn_H6gt3aQ/edit)：页面与用户需求，访问需要相应权限。

## 当前技术方向

业务后端采用 Django + Django REST Framework，业务数据使用 PostgreSQL；Agent 作为独立 Python 服务，通过后端 API 领取任务、读取上下文并提交分析结果。

RAG 拟采用 pgvector，复杂助手流程拟采用 LangGraph。文档处理、对象存储、Celery + Redis 和 Langfuse 按对应功能需要分阶段引入，具体范围见技术选型文档。
