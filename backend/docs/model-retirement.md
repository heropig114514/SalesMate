# 模型清理、远程恢复与当前方案

2026-09-25 用户明确要求只保留正在使用的图谱模型，退役探索性范式的模型和代码一并清理。当前应用保留 PostgreSQL 业务投影、48 类 schema、来源血缘、Qwen 文本候选、实体解析及九项 `graph.*` 工具。运行与 Agent 接入见[交接入口](semantic-agent-handoff.md)。

## 保留的唯一项目模型

从 SalesMate 仓库根目录定位：

```text
../output/semantic-finetune-v2-20260925/results/semantic-ft-v2/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf
```

大小 2,497,278,784 字节，约 2.326 GiB；SHA256：

```text
cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09
```

服务器运行命令已核对为 `/opt/salesmate-semantic/models/` 下的同名文件，variant 为 semantic-v2、profile 为 baseline。本次清理本地模型与项目代码，不停止服务器、不删除服务器制品或修改远程运行配置。启动器仅接受 semantic-v2，默认也改为该保留模型；端口、线程、上下文、生成预算、种子、超时及验证门槛均未更改。这个收敛由用户清理要求触发，不代表质量评估升级或新模型上线。

## 远程实际保存了什么

| 制品 | 已核对的恢复位置 | 本次核验范围 |
|---|---|---|
| 微调 V2 Q4 GGUF | [私有 Kaggle Notebook 输出](https://www.kaggle.com/code/heropig/salesmate-semantic-qlora-v2/output) | COMPLETE；下载 HTTP 200、真实长度与 GGUF 文件头匹配，本地完整 SHA256 匹配；未重复完整下载 2.5 GB |
| LoRA 适配器 | 同一 Notebook 的 `semantic-ft-v2/adapter/` | 23,631,240 字节已流式完整下载计算 SHA256，与本地相同；未保存第二份模型 |
| 官方原始 HF 权重 | [Qwen 固定版本](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/tree/cdbee75f17c01a7cc42f958dc650907174af0554) | 固定 revision 元数据可访问，三片 LFS 的大小与 SHA256 均与本地逐文件校验一致 |
| 原版 F16/Q4、微调合并 F16 | 本次 Notebook 没有保留这些完整文件 | 需从官方原始权重及 LoRA 重新转换/合并，不宣称可以从 Notebook 直接下载 |

LoRA SHA256 为 `be1f546a173433fc9ca7aa0187ad1471d193d63ca982b4c167fa278bb9748090`。官方 revision 为 `cdbee75f17c01a7cc42f958dc650907174af0554`。Kaggle CLI 文件列表显示的 size 与真实下载响应不一致，本次用下载 HTTP Content-Length 和实际字节校验，未把列表中的数百字节当作模型大小。

重新下载当前模型（从仓库根目录执行，Kaggle CLI 已认证）：

```powershell
kaggle kernels output heropig/salesmate-semantic-qlora-v2 `
  --path ../output/semantic-finetune-v2-20260925/results `
  --file-pattern 'Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M\.gguf$' --page-size 200
Get-FileHash ../output/semantic-finetune-v2-20260925/results/semantic-ft-v2/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf -Algorithm SHA256
```

需要再次微调或重建 F16 时再取回适配器及官方权重，不为运行图谱预先下载它们：

```powershell
kaggle kernels output heropig/salesmate-semantic-qlora-v2 `
  --path ../output/semantic-finetune-v2-20260925/results `
  --file-pattern '(^|/)adapter/' --page-size 200
python ../output/crmarena-cpu-q4-20260924/download_model.py
```

第二条使用保留的固定 revision 下载脚本和原实验摘要清单，逐文件校验官方模型；需要 requests。工作区中的官方 llama.cpp 转换源码、CPU 二进制和运行依赖继续保留，供当前 GGUF 推理与显式恢复使用，目录名中的历史日期不表示仍部署旧研究接口。新 checkout 应按[部署说明](semantic-graph-deployment.md)安装官方工具并准备模型，而不是假设 Git 含大文件。

Notebook 后续更新可能改变最新输出，下载后必须比对本页固定摘要；不匹配则停止，不能通过修改摘要或自动换模型绕过。远程记录说明当前可恢复状态，不是永久存储保证。历史完整制品核验脚本需要相关文件，清理后运行前必须显式恢复，不能把“缺少已删除权重”解释为模型损坏。

## 退役范围

- 从应用移除 CRMArena 冻结公开研究的服务、HTTP 路由、四项 MCP 工具、GPU 配置和安装器，以及仅为这些接口存在的测试和接入文档。
- 清理本地未发布的 EASE 公开商品推荐服务、路由、权重、安装器、测试和配置；当前语义图谱不依赖该模型。
- 清理本地旧 G-reasoner、LightGCN、RelationalSAGE/NoGraphMLP/ShuffledSAGE、EASE/ItemKNN 实验权重及相应实验代码。相关 Kaggle Notebook 输出目录已逐页核对，旧模型远程仍有记录；这不表示每份旧制品均重新完整下载验证。
- 清理官方 HF 三片权重、官方 F16、官方 Q4、重复 Q4 和本地 LoRA，只保留上述微调 Q4。保留历史报告、指标、原始评测响应、数据、当前训练/评测代码及恢复所需元数据，不重写历史实验结论。
- 当前知识图谱的 models、迁移、projection、sync、捕获 Worker 和 L1 证据规则继续使用，不属于退役模型。账号、业务数据、外部模型服务配置和其他项目的共享缓存不在本次清理范围内。

旧 API 不转发到新工具，不保留降级或兼容实现；调用旧路径将无匹配，旧工具名不再出现在目录中。持有旧白名单的凭证不会自动获得新工具权限。历史版本代码可从 Git 保留的 `semantic-graph-v0.2.0` 和 PR 历史查看；本次不强推、删除远程 Notebook 或重写 Git 历史。

逐文件删除清单（绝对路径、字节数、摘要）、远程核验与实际释放量保存在工作区 `../output/model-cleanup-20260925/`，不含令牌或签名下载 URL。清理后的验证结果记录于该目录；部署版本与仓库最新提交需分别核对。

## 实际清理与验证

2026-09-25 用户完成两阶段手动清理，Agent 核对回执与磁盘：53 个模型/缓存文件、719 个旧代码/制品文件均已不存在，共 772 个文件、21,891,725,417 字节（约 20.39 GiB）。保留模型的完整 SHA256 与上文一致。配套补丁已应用于主工作区及发布 worktree；发布提交仅包含本轮清理，主工作区其他业务改动保持原状。

- 发布 worktree 的 Django 回归 61 项通过，使用独立的真实 PostgreSQL 测试库，覆盖当前图谱、语义输入、业务工具、推理配置、训练契约及退役路由/工具；系统检查无异常。语义模型调用使用测试替身，不能据此声称真实模型生成质量或服务延迟已重新验证。
- SDK/MCP 回归 8 项通过，覆盖客户端及 stdio MCP 到测试 HTTP 服务的桥接；不是线上模型调用测试。
- 主工作区与发布 worktree 的后端文档/目录检查各覆盖 264 个 Python 文件并通过；发布 worktree SDK 检查覆盖 8 个文件并通过。变更文件的说明、目录、变量索引已人工核对，`git diff --check` 通过。
- 本轮未重新训练、修改实验评价条件或部署服务器；服务器仍使用此前的 semantic-v2 / baseline 配置。Git 提交与远程分支核对结果见工作区审计记录。
