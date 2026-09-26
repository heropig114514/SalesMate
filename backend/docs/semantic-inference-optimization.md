# Semantic graph CPU inference optimization experiment

The user authorized this optimization. Original fine-tuning, weights, corpus, and scoring rules remained frozen. Deployment defaults to baseline. Profiles are explicit experiment choices, not failure fallbacks.

**Result: prefix8 substantially accelerates cached inference but failed full-test adoption criteria and is not recommended as default.** All results remain. Temporary performance validation used an isolated server; administrators explicitly restored baseline afterward. Restoration was an end-of-experiment configuration action, never runtime fallback.

## Prespecified comparison

Use identical fine-tuned Q4_K_M weights (SHA256 `cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09`), llama.cpp b11146, complete 48-type schema, temperature=0, seed=2026, 1536 output tokens, and 300-second model timeout. Fixed development cases: need_budget, contact_not_employee, negated_need. Tests retain all original 12 cases. No label changes, fabricated outputs, or shortened schemas.

Compare baseline, context8, flash8, kv8, prefix16, combined8 sequentially; definitions reside in apps/knowledge_graph/inference_profiles.py. Each gets a fresh single-slot service. The first sample is cold, immediately repeated for warm-cache measurement; subsequent different inputs measure cross-input reuse. Repeats do not contribute to quality summaries. Cold means no preceding request KV cache, excluding startup/load time and not guaranteeing cold OS file caches.

| Profile | Context | KV type | Flash Attention | Prefix reuse/message order |
|---|---:|---|---|---|
| baseline | 16384 | F16 | auto | Off, original order |
| context8 | 8192 | F16 | auto | Off, original order |
| flash8 | 8192 | F16 | on | Off, original order |
| kv8 | 8192 | Q8_0 | on | Off, original order |
| prefix16 | 16384 | F16 | auto | On, schema first |
| combined8 | 8192 | Q8_0 | on | On, schema first |
| prefix8 (supplemental) | 8192 | F16 | auto | On, schema first |

Selection rules were recorded after baseline's first/repeat requests but before other results: across three nonrepeated development cases, candidates must have valid, facts_exact, and TP no lower than baseline, FP/FN no higher, and no runtime failures. Choose the qualifying candidate with fastest nonrepeat mean; retain baseline if unimproved. Report measured RSS, cold/repeated latency, and actual cache_n separately, not theoretical memory substitutes.

At 09:40 UTC+8 on 2026-09-25, after the first Q8 cold development request took 131.75 seconds and before test evaluation, supplemental prefix8 was declared. Hypothesis: retain 8K capacity benefits while avoiding this CPU's slower Q8 KV prefill. The original six groups stayed unchanged. The supplemental group used a separate output directory and the same three development/repeat rules, competing under the same quality/mean-latency criteria. This extends search based on development results, not an entirely prespecified six-group comparison. Original/all-round protocols remain. Tests still evaluate only baseline/final candidate without choosing a runner-up from test results.

Write selection.json before running complete 12-case baseline/candidate tests. Recommend adoption only under the same quality gate. Tests never tune parameters, choose second place, or trigger additional training. On regression retain baseline and explicitly report failure. Even unchanged relative quality on small synthetic shared-template samples does not establish real-email quality.

## Implementation boundaries

schema-first-v1 only reorders user-message JSON keys: full schema precedes dynamic candidates/time/text. System instructions remain unchanged; data is not promoted to instructions. KV reuse shares actual token prefixes, not graph answers, and skips no authorization, evidence, transaction, or idempotency checks. Logically similar requests only reuse identical prefixes; single-slot queues/context changes may reduce benefits.

8K reduces maximum context; Q8 changes KV precision; Flash Attention/cache change computation and may alter outputs. Context shifting, automatic fit, truncation, and automatic profile fallback are prohibited. Oversized inputs/service failures fail explicitly. Launcher --profile and API GRAPH_LLM_PROFILE must match. Audit requested_inference_config records requested settings, not a substitute for verifying /props, startup logs, and actual service commands.

## Reproduction

Requires Python, requests, jsonschema, psutil, frozen data, and official CPU llama-server. See backend/tools/benchmark_graph_inference.py --help for required paths. Initial screening uses `--profiles baseline context8 flash8 kv8 prefix16 combined8 --split dev`; supplemental screening uses a new directory with `--profiles prefix8 --split dev`; full tests use `--profiles baseline prefix8 --split test`. Every --output must be new. Raw requests/responses contain synthetic samples but remain experiment materials.

The benchmark verifies model/corpus hashes and stores protocols, launch commands, raw responses, scores, and process-tree RSS every two seconds. Desktop load was not isolated; one run provides no statistical confidence interval and RSS can double-count shared pages. Report tokens/prefill/decode to separate computation gains from output-length changes. Servers remeasure with their own 2 threads; desktop 8-thread figures are not server performance.

## Runtime configuration

Set launcher `--profile <name>` and API `GRAPH_LLM_PROFILE=<same-name>`. Both default to baseline; unknown names fail. Changing API environment alone cannot change a running model's context/KV type; restart consistently. Verify launch commands, /props actual context/slots, and response timings.cache_n. Sources retain model_audit.inference_profile, requested_inference_config, and timings.

Only to reproduce the candidate that failed adoption:

```bash
python backend/tools/run_graph_model.py \
  --executable /absolute/path/llama-server \
  --model /absolute/path/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf \
  --variant semantic-v2 --threads 2 --port 8088 --profile prefix8
```

API settings: GRAPH_LLM_URL=http://127.0.0.1:8088/v1, GRAPH_LLM_MODEL=salesmate-graph, GRAPH_LLM_PROFILE=prefix8. HTTP/MCP parameters stay unchanged; the model binds loopback. Two threads target this server; local comparisons explicitly retain eight.

Restore baseline explicitly at both ends and restart. Administrators choose this action; timeouts never trigger it automatically. Restart clears KV cache, requiring full first-request prefill; healthy services do not imply warmed prefixes. The 80000-character business limit does not guarantee fitting 8K tokens. Large inputs may fail without truncation or hidden context expansion.

## Results

Development screening selected prefix8. Seconds below; RSS is sampled process-tree peak GiB:

| Profile | 3 nonrepeat mean | First cold | Identical repeat | RSS |
|---|---:|---:|---:|---:|
| baseline | 89.09 | 91.45 | 97.88 | 6.38 |
| context8 | 62.38 | 66.30 | 67.36 | 5.26 |
| flash8 | 64.48 | 69.14 | 67.83 | 5.26 |
| kv8 | 129.71 | 131.75 | 131.38 | 4.73 |
| prefix16 | 31.64 | 64.49 | 19.58 | 6.38 |
| combined8 | 57.57 | 139.91 | 19.38 | 4.73 |
| prefix8 | 30.95 | 63.02 | 17.84 | 5.26 |

All seven groups had identical quality on these three cases: 2 contract passes, 1 exact-fact case, TP=0/FP=1/FN=3. This does not mean accurate graphs: the first omitted a relation target, the contact case had a wrong relation, and the correct case was negated product need. Cached repeats reused 4138 tokens; later different inputs reused 4116. Q8 KV substantially slowed prefill on this CPU. The approximately 2% prefix8/prefix16 mean difference is not statistically established speed superiority; prefix8 also used less memory.

Full 12-case results, excluding repeats from quality/means:

| Metric | baseline | prefix8 |
|---|---:|---:|
| Mean model HTTP seconds | 62.47 | 18.31 |
| First request without KV cache, seconds | 70.13 | 66.70 |
| Identical repeat, seconds | 70.03 | 22.52 |
| Mean prefill seconds | 46.05 | 4.70 |
| Mean decode seconds | 16.40 | 13.60 |
| Mean generated tokens | 135.67 | 126.42 |
| Peak sampled RSS GiB | 6.40 | 5.26 |
| Contract passes | 8/12 | 7/12 |
| Exact facts | 6/12 | 6/12 |
| TP / FP / FN | 4 / 2 / 8 | 4 / 0 / 8 |
| Fact micro-F1 | 0.444 | 0.500 |

Mean speedup was about 3.41×, primarily shared-prefix computation reuse. Some outputs shortened, so not all gains are kernel acceleration. The cold request lacked comparable gains. The contact case changed from valid-but-wrong relations to rejection for missing targets; ambiguous names reduced false positives but still missed facts. Despite improved FP/F1, fewer contract passes **failed prespecified adoption criteria**. No runner-up selection, test-driven retuning, or relaxed validation occurred.

Local timing spans model HTTP request/response, including outputs later rejected by scoring, excluding database writes. Therefore 18.31 seconds is not mean successful graph-API latency.

46 database/contract regressions passed; after adding prefix8, 7 configuration tests passed again. Documentation passed for 270 backend/11 integration Python files; comment-change checks had 0 errors/0 review items and migrations were unchanged. The first database run encountered stopped WSL PostgreSQL; the second encountered interrupted-run residue. After keeping WSL running/cleaning the test database, all 46 passed together; failure logs remain.

Raw benchmarks: local output/semantic-optimization-20260925/. Public summaries/per-case timing/scoring: [verification record](semantic-inference-verification.json).

## Temporary server validation

Code commit a5c60d0f029b00d6d853fcfc42904bd5e54d9635 was pushed before deployment into the existing isolated directory; SHA256 verified all 502 Git source files. The launcher reverified weights; /props showed 8192 context/1 slot, API logs confirmed prefix8, and CPU threads=2. Tests touched neither production databases nor site code.

- Initialization took about 83.82 seconds after digest verification; loading is excluded from request latency.
- Anonymous Tools returned 401; 48-schema discovery, structured input, and source replay passed.
- Cold natural-language input returned **HTTP 502 / ReadTimeout after 300.28 seconds**. Logs showed 2048 input tokens in 243.96 seconds (8.39 tokens/s), before completing the full input.
- Queries covered all 4 sandbox sources and confirmed no text source persisted; no retry. After client cancellation, explicit model shutdown stopped unfinished computation.
- Model cgroup snapshots showed about 3.20 GB RAM/2.89 GB swap. Valid vmstat intervals showed roughly 10–22 MiB/s swap-in and 17%–31% I/O wait, indicating memory/paging pressure. cgroup snapshots are not comparable on identical terms to local process-tree RSS.
- Real stdio MCP connected to real HTTP for nine-tool discovery, structured writes, replay, queries, and retraction; **this MCP smoke invoked no LLM**.

Server warm/concurrent/long-input tests were not completed; natural-language graph availability is not established. Original site readiness stayed ok. The candidate failed local adoption and server cold-timeout goals, so baseline was restored explicitly after the experiment. No raised 300-second threshold, model switching, or answer repair.

Isolated servers retain app symlinks to releases/<Git-commit>, with deployed-revision recording actual source. Model settings reside in systemd inference-profile.conf drop-ins; API settings in private runtime.env; align both. Gunicorn 26.2 requires a private writable control directory. `/opt/salesmate-semantic/.gunicorn` was created as 0700 owned by salesmate-graph, fixing startup permission errors.

Future work may separately test reduced weight-repack memory, batch workspaces, or smaller models, retaining these results as historical baseline. None were tested here, and test-set profile search did not continue merely to obtain a pass.
