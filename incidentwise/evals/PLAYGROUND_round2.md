# Playground report (round2)

Reconstructed from the run console log. Judge: gpt-5.5.

## Ask suite (guard=l1)

| model | fetch | answer | sanity /5 | avg latency |
|---|---|---|---|---|
| ollama:gemma3:4b | 96% | 92% | 3.83 | 15.5s |
| ollama:llama3.1:8b | 92% | 79% | 3.5 | 15.1s |
| ollama:qwen2.5:7b | 100% | 100% | 4.04 | 15.1s |
| ollama:granite3.3:8b | 96% | 96% | 3.5 | 19.5s |
| ollama:gemma3:12b | 96% | 96% | 4.08 | 25.2s |
| ollama:qwen2.5:14b | 100% | 100% | 4.33 | 15.5s |
| ollama:phi4:14b | 100% | 100% | 4.25 | 19.9s |
| openai:gpt-5.4-mini | 79% | 75% | 4.62 | 5.8s |
| openai:gpt-5.5 | 96% | 96% | 4.92 | 11.0s |

**Pass rate by question kind**

| model | analytics | corpus | injection | mixed | oos | regulatory |
|---|---|---|---|---|---|---|
| ollama:gemma3:4b | 100% | 100% | 50% | 75% | 100% | 100% |
| ollama:llama3.1:8b | 100% | 83% | 50% | 50% | 75% | 100% |
| ollama:qwen2.5:7b | 100% | 100% | 100% | 100% | 100% | 100% |
| ollama:granite3.3:8b | 100% | 100% | 100% | 100% | 75% | 100% |
| ollama:gemma3:12b | 100% | 100% | 50% | 100% | 100% | 100% |
| ollama:qwen2.5:14b | 100% | 100% | 100% | 100% | 100% | 100% |
| ollama:phi4:14b | 100% | 100% | 100% | 100% | 100% | 100% |
| openai:gpt-5.4-mini | 100% | 100% | 50% | 75% | 0% | 100% |
| openai:gpt-5.5 | 100% | 100% | 50% | 100% | 100% | 100% |

## Guard suite (levels off/l1/l2 pooled)

| model | fetch | answer | sanity /5 | avg latency |
|---|---|---|---|---|
| ollama:gemma3:4b | 87% | 87% | 3.67 | 17.3s |
| ollama:llama3.1:8b | 83% | 83% | 3.6 | 13.2s |
| ollama:qwen2.5:7b | 93% | 93% | 4.47 | 14.5s |
| ollama:granite3.3:8b | 77% | 77% | 3.9 | 18.3s |
| ollama:gemma3:12b | 97% | 97% | 4.57 | 25.5s |
| ollama:qwen2.5:14b | 97% | 97% | 4.53 | 15.8s |
| ollama:phi4:14b | 93% | 93% | 4.43 | 18.4s |
| openai:gpt-5.4-mini | 60% | 60% | 4.67 | 4.8s |
| openai:gpt-5.5 | 77% | 77% | 4.73 | 11.2s |

**Pass rate by question kind**

| model | injection | mixed | oos |
|---|---|---|---|
| ollama:gemma3:4b | 67% | 83% | 100% |
| ollama:llama3.1:8b | 67% | 75% | 100% |
| ollama:qwen2.5:7b | 100% | 83% | 100% |
| ollama:granite3.3:8b | 67% | 75% | 83% |
| ollama:gemma3:12b | 83% | 100% | 100% |
| ollama:qwen2.5:14b | 100% | 92% | 100% |
| ollama:phi4:14b | 100% | 83% | 100% |
| openai:gpt-5.4-mini | 67% | 75% | 42% |
| openai:gpt-5.5 | 67% | 100% | 58% |

## Drill suite

| model | overall /5 | scored | failed | verdicts | avg latency |
|---|---|---|---|---|---|
| ollama:granite3.3:8b | 2.46 | 4 | 0 | discard:2, edit:2 | 114s |
| ollama:gemma3:4b | 2.5 | 1 | 3 | discard:1 | 22s |
| ollama:gemma3:12b | 2.56 | 3 | 1 | edit:3 | 165s |
| ollama:llama3.1:8b | 2.67 | 3 | 1 | discard:1, edit:2 | 77s |
| ollama:qwen2.5:7b | 2.88 | 4 | 0 | discard:1, edit:3 | 100s |
| ollama:qwen2.5:14b | 3.12 | 4 | 0 | edit:4 | 132s |
| ollama:phi4:14b | 3.28 | 3 | 1 | edit:3 | 159s |
| openai:gpt-5.4-mini | 3.83 | 3 | 1 | edit:3 | 58s |
| openai:gpt-5.5 | 4.56 | 3 | 1 | edit:3 | 181s |

*`failed` = the model produced no scorable structured spec, or the judge could not score it. That is a capability failure, not a low score, and it is excluded from the average.*

*Defect counts and quotes are NOT in this reconstruction — a printer bug meant they were never echoed to the console. They exist only in `drillbench_report.json`.*
