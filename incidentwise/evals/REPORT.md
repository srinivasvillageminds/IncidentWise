# Eval report

Golden set: golden_seed.jsonl (27 items) | k=6 | model: llama3.1:8b

## Retrieval

| mode | hit@k | category-hit | term-hit | avg latency |
|---|---|---|---|---|
| medium | 1.0 | 1.0 | 0.938 | 0.33s |
| good | 1.0 | 1.0 | 0.938 | 0.13s |
| best | 1.0 | 0.938 | 0.938 | 3.65s |

## Router: accuracy 1.0

## Answers (mode=good, judge=openai:gpt-5.5)

| answering model | refusal correctness | grounding |
|---|---|---|
| ollama:llama3.1:8b | **1.0** | **0.0** |
| ollama:qwen2.5:7b | **1.0** | **0.25** |
| ollama:granite3.3:8b | **0.857** | **0.0** |
| ollama:gemma3:12b | **1.0** | **0.0** |
| ollama:qwen2.5:14b | **1.0** | **0.0** |
| ollama:phi4:14b | **1.0** | **0.0** |
| openai:gpt-5.4-mini | **1.0** | **0.0** |

*Retrieval and router scores above are model-independent — they involve no LLM.*

## Suggested publish gate

hit_rate(good) >= 0.8 | router >= 0.9 | refusal >= 0.8 | grounding >= 0.9

Failures listed in report.json. A MISS is a to-do: fix retrieval, fix the prompt, or fix a badly-written golden item - decide which, honestly.
