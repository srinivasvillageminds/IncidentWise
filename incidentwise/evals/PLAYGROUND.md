# Playground report

models: ollama:llama3.1:8b, ollama:qwen2.5:7b, ollama:gemma3:12b, ollama:qwen2.5:14b, openai:gpt-5.4-mini, openai:gpt-5.5 | judge: openai:gpt-5.5 | 2026-07-12 19:16 UTC

## Ask suite (guard=l1, judge=openai:gpt-5.5)

| model | fetch | answer | sanity /5 |
|---|---|---|---|
| ollama:llama3.1:8b | 96% | 92% | 3.62 |
| ollama:qwen2.5:7b | 100% | 96% | 4.21 |
| ollama:gemma3:12b | 96% | 92% | 3.92 |
| ollama:qwen2.5:14b | 100% | 96% | 4.42 |
| openai:gpt-5.4-mini | 75% | 67% | 4.67 |
| openai:gpt-5.5 | 96% | 88% | 4.83 |

**ollama:llama3.1:8b** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 100% · mixed 75% · injection 50%
**ollama:qwen2.5:7b** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 100% · mixed 75% · injection 100%
**ollama:gemma3:12b** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 100% · mixed 75% · injection 50%
**ollama:qwen2.5:14b** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 100% · mixed 75% · injection 100%
**openai:gpt-5.4-mini** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 0% · mixed 50% · injection 0%
**openai:gpt-5.5** by question kind: corpus 100% · regulatory 100% · analytics 100% · oos 75% · mixed 50% · injection 100%

## Guard suite (guard=-, judge=openai:gpt-5.5)

| model | fetch | answer | sanity /5 |
|---|---|---|---|
| ollama:llama3.1:8b @ off | 90% | 90% | 0 |
| ollama:qwen2.5:7b @ off | 100% | 100% | 0 |
| ollama:gemma3:12b @ off | 100% | 100% | 0 |
| ollama:qwen2.5:14b @ off | 80% | 80% | 0 |
| openai:gpt-5.4-mini @ off | 30% | 30% | 0 |
| openai:gpt-5.5 @ off | 60% | 60% | 0 |
| ollama:llama3.1:8b @ l1 | 80% | 80% | 0 |
| ollama:qwen2.5:7b @ l1 | 80% | 80% | 0 |
| ollama:gemma3:12b @ l1 | 100% | 100% | 0 |
| ollama:qwen2.5:14b @ l1 | 80% | 80% | 0 |
| openai:gpt-5.4-mini @ l1 | 30% | 30% | 0 |
| openai:gpt-5.5 @ l1 | 60% | 60% | 0 |
| ollama:llama3.1:8b @ l2 | 90% | 90% | 0 |
| ollama:qwen2.5:7b @ l2 | 80% | 80% | 0 |
| ollama:gemma3:12b @ l2 | 100% | 100% | 0 |
| ollama:qwen2.5:14b @ l2 | 80% | 80% | 0 |
| openai:gpt-5.4-mini @ l2 | 80% | 80% | 0 |
| openai:gpt-5.5 @ l2 | 80% | 80% | 0 |


## Drill suite (judge=openai:gpt-5.5)

| model | overall /5 | absurdities per drill | verdicts |
|---|---|---|---|
| ollama:llama3.1:8b | 2.79 | 5.8 | edit:3, discard:1 |
| ollama:qwen2.5:7b | 3.17 | 5.5 | edit:4 |
| ollama:qwen2.5:14b | 3.33 | 3.5 | edit:3, ?:1 |
| openai:gpt-5.4-mini | 4.39 | 2.0 | use:1, edit:2 |
| openai:gpt-5.5 | 4.89 | 0.7 | use:3 |

**ollama:llama3.1:8b** by axis: causality_realism 2.75 · internal_consistency 2.5 · role_accuracy 2.0 · physical_plausibility 2.75 · no_fabrication 4.0 · training_value 2.75
**ollama:qwen2.5:7b** by axis: causality_realism 3.25 · internal_consistency 3.0 · role_accuracy 2.75 · physical_plausibility 3.0 · no_fabrication 3.75 · training_value 3.25
**ollama:qwen2.5:14b** by axis: causality_realism 3.33 · internal_consistency 3.0 · role_accuracy 2.67 · physical_plausibility 3.33 · no_fabrication 4.67 · training_value 3.0
**openai:gpt-5.4-mini** by axis: causality_realism 4.67 · internal_consistency 4.67 · role_accuracy 4.0 · physical_plausibility 4.67 · no_fabrication 4.0 · training_value 4.33
**openai:gpt-5.5** by axis: causality_realism 5.0 · internal_consistency 5.0 · role_accuracy 5.0 · physical_plausibility 4.33 · no_fabrication 5.0 · training_value 5.0

---
*Fetch and Answer are deterministic checks. Sanity and drill scores come from an LLM judge - use a strong judge model, and treat these as screening, not ground truth: a competent safety professional remains the validator.*
