# Kaggle graph training source copy

This directory contains an unchanged source snapshot of the SalesMate graph
extraction model's QLoRA V2 training experiment for the competition code submission.

- Source: [SalesMate Semantic QLoRA V2](https://www.kaggle.com/code/heropig/salesmate-semantic-qlora-v2)
- Retrieved: 28 September 2026 using `kaggle kernels pull heropig/salesmate-semantic-qlora-v2 -m`.
- `salesmate-semantic-qlora-v2.py`: original Kaggle Python script, including its
  Chinese implementation comments and declaration index.
- `kernel-metadata.json`: Kaggle export of the source identity and runtime settings.
- Source SHA-256: `35f2e41c367d49d656cca7023fb2c6b345cce2126da6b0cc4d0028e2545eccf6`.

The script trains a Qwen3-4B-Instruct-2507 LoRA adapter on 96 synthetic examples
for 24 optimizer updates, merges the adapter, creates F16/Q4 model artifacts,
and evaluates the original and fine-tuned models under the same inference engine.
All original seeds, model revisions, hyperparameters and evaluation conditions
are preserved. This copy was not executed or retrained when added to the repository.

## Runtime requirements

Run in the original Kaggle GPU environment with Internet access and access to
the private dataset `heropig/salesmate-semantic-synthetic-v1`. The script expects
Kaggle input mounts, two NVIDIA T4 GPUs (training uses GPU 0), and fresh output/scratch directories. It installs
its pinned Python dependencies and builds the pinned llama.cpp revision itself.
The original metadata retains the dataset reference, container image and hardware
setting. Downloaded model weights and dataset contents are not included in this
source-only copy.

The repository also maintains a documented implementation at
[`backend/tools/semantic_finetune_kernel.py`](../../../backend/tools/semantic_finetune_kernel.py).
Experiment results and limitations are described in
[`backend/docs/semantic-model-experiments.md`](../../../backend/docs/semantic-model-experiments.md).
This snapshot preserves the Kaggle source separately for submission provenance.
