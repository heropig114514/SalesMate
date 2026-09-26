# Model retirement, remote recovery, and current architecture

On 2026-09-25, the user explicitly requested retaining only the graph model in use and removing both models and code for retired exploratory approaches. The application retains PostgreSQL business projections, the 48-type schema, source lineage, Qwen text candidates, entity resolution, and nine `graph.*` tools. See the [handoff entry point](semantic-agent-handoff.md) for runtime and Agent integration.

## The sole retained project model

Locate it relative to the SalesMate repository root:

```text
../output/semantic-finetune-v2-20260925/results/semantic-ft-v2/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf
```

Size: 2,497,278,784 bytes, approximately 2.326 GiB. SHA256:

```text
cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09
```

The verified server command uses the same filename under `/opt/salesmate-semantic/models/`, variant semantic-v2 and profile baseline. This cleanup covers local models/project code without stopping servers, deleting server artifacts, or changing remote runtime configuration. The launcher accepts only semantic-v2 and defaults to the retained model; port, threads, context, generation budget, seed, timeout, and validation thresholds are unchanged. This consolidation follows the user's cleanup request and does not constitute a quality-evaluation upgrade or new model deployment.

## What is actually retained remotely

| Artifact | Verified recovery location | Verification scope |
|---|---|---|
| Fine-tuned V2 Q4 GGUF | [Private Kaggle Notebook output](https://www.kaggle.com/code/heropig/salesmate-semantic-qlora-v2/output) | COMPLETE; HTTP 200 download, actual length, and GGUF header matched; complete local SHA256 matched. The full 2.5 GB was not downloaded again. |
| LoRA adapter | `semantic-ft-v2/adapter/` in the same Notebook | Streamed all 23,631,240 bytes and computed a SHA256 matching local data, without saving another model copy. |
| Official original HF weights | [Pinned Qwen revision](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/tree/cdbee75f17c01a7cc42f958dc650907174af0554) | Pinned revision metadata accessible; sizes/SHA256 of all three LFS shards matched local per-file verification. |
| Original F16/Q4 and fine-tuned merged F16 | These complete files were not retained by this Notebook | Reconvert/merge from official original weights and LoRA; direct Notebook download availability is not claimed. |

LoRA SHA256: `be1f546a173433fc9ca7aa0187ad1471d193d63ca982b4c167fa278bb9748090`. Official revision: `cdbee75f17c01a7cc42f958dc650907174af0554`. Kaggle CLI listing sizes differed from actual download responses; verification used HTTP Content-Length and actual bytes, not the few hundred bytes shown in listings as model sizes.

Redownload the current model from the repository root with an authenticated Kaggle CLI:

```powershell
kaggle kernels output heropig/salesmate-semantic-qlora-v2 `
  --path ../output/semantic-finetune-v2-20260925/results `
  --file-pattern 'Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M\.gguf$' --page-size 200
Get-FileHash ../output/semantic-finetune-v2-20260925/results/semantic-ft-v2/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf -Algorithm SHA256
```

Retrieve adapters/official weights only for explicit retraining or F16 rebuilding; they are not prerequisites to running the graph:

```powershell
kaggle kernels output heropig/salesmate-semantic-qlora-v2 `
  --path ../output/semantic-finetune-v2-20260925/results `
  --file-pattern '(^|/)adapter/' --page-size 200
python ../output/crmarena-cpu-q4-20260924/download_model.py
```

The second command uses the retained pinned-revision download script and original experiment digest manifest to verify official weights file by file; requests is required. Official llama.cpp conversion sources, CPU binaries, and runtime dependencies remain in the workspace for current GGUF inference and explicit recovery. Historical dates in directory names do not imply retired research APIs remain deployed. New checkouts should install official tools and prepare models according to [deployment instructions](semantic-graph-deployment.md), rather than assuming Git contains large artifacts.

Later Notebook updates may change current outputs. Always compare downloads against the fixed digests on this page; stop on mismatch instead of changing digests or automatically switching models. Remote records describe current recoverability, not permanent storage guarantees. Historical full-artifact verification scripts require those files; explicitly restore removed weights before running them, rather than interpreting their absence as corruption.

## Retirement scope

- Removed the frozen public CRMArena research service, HTTP routes, four MCP tools, GPU configuration/installer, and tests/integration documents existing solely for those interfaces.
- Removed the locally unpublished EASE public-product recommendation service, routes, weights, installer, tests, and configuration; the current semantic graph has no dependency on it.
- Removed local legacy G-reasoner, LightGCN, RelationalSAGE/NoGraphMLP/ShuffledSAGE, and EASE/ItemKNN experiment weights and corresponding code. Related Kaggle Notebook output directories were inspected page by page and still contain remote records; this does not mean every old artifact was downloaded and verified in full again.
- Removed the three official HF weight shards, official F16/Q4, duplicate Q4, and local LoRA, retaining only the fine-tuned Q4 above. Historical reports, metrics, raw evaluation responses, data, current training/evaluation code, and recovery metadata remain; historical conclusions were not rewritten.
- Current knowledge-graph models, migrations, projection, sync, capture Worker, and L1 evidence rules remain in use and are outside retirement scope. Accounts, business data, external model-service configuration, and other projects' shared caches were excluded.

Old APIs do not forward to new tools; no fallback or compatibility implementations remain. Old paths have no match, and old tool names no longer appear in catalogs. Credentials with old allowlists gain no new tool permissions automatically. Historical code remains available through Git tag `semantic-graph-v0.2.0` and PR history; no force push, remote Notebook deletion, or Git history rewriting occurred.

Per-file deletion manifests (absolute paths, byte sizes, digests), remote verification, and actual reclaimed space are retained under workspace `../output/model-cleanup-20260925/`, without tokens or signed download URLs. Post-cleanup validation is recorded there; verify deployed versions separately from the latest repository commit.

## Actual cleanup and validation

On 2026-09-25, the user completed two-stage manual cleanup and Agent checked receipts/disk: 53 model/cache files and 719 retired code/artifact files were absent, totaling 772 files and 21,891,725,417 bytes (approximately 20.39 GiB). The retained model's complete SHA256 matches above. Associated patches were applied to the main workspace and release worktree; the release commit contained only this cleanup, preserving unrelated main-workspace business changes.

- 61 Django regressions passed in the release worktree using an independent real PostgreSQL test database, covering the current graph, semantic inputs, business tools, inference configuration, training contracts, and retired routes/tools. System checks were clean. Semantic calls used test doubles, so these results do not revalidate real model quality or latency.
- 8 SDK/MCP regressions passed, covering clients and the stdio MCP bridge to a test HTTP service; these were not live model-call tests.
- Backend documentation/directory checks passed over 264 Python files in both main and release worktrees; release SDK checks passed over 8 files. Modified descriptions, directories, and variable indexes were manually reviewed; `git diff --check` passed.
- No retraining, evaluation-condition changes, or server deployment occurred; the server retained its previous semantic-v2 / baseline configuration. See workspace audit records for Git commit and remote-branch verification.
