# Eval report (round2)

Reconstructed from the run console log. Judge: gpt-5.5 (outside the tested set for all local models).

## Retrieval (model-independent — no LLM involved)

| mode | hit@6 | avg latency |
|---|---|---|
| good | 1.0 | 0.35s |

*Ceiling effect: the 16-question golden set is too easy to discriminate retrieval modes. Harder questions needed before these numbers can adjudicate anything.*

## Router accuracy: 1.0

## Answers — refusal & grounding

| model | refusal | grounding (mean) | grounding (pooled) | claims supported |
|---|---|---|---|---|
| ollama:gemma3:4b | **1.0** | **0.674** | 0.633 | 57/90 |
| ollama:llama3.1:8b | **1.0** | **0.53** | 0.557 | 54/97 |
| ollama:qwen2.5:7b | **1.0** | **0.664** | 0.734 | 47/64 |
| ollama:granite3.3:8b | **0.857** | **0.671** | 0.685 | 89/130 |
| ollama:gemma3:12b | **1.0** | **0.561** | 0.581 | 75/129 |
| ollama:qwen2.5:14b | **1.0** | **0.618** | 0.6 | 45/75 |
| ollama:phi4:14b | **1.0** | **0.543** | 0.623 | 76/122 |
| openai:gpt-5.4-mini | **1.0** | **0.63** | 0.648 | 94/145 |
| openai:gpt-5.5 | **1.0** | **0.619** | 0.662 | 149/225 |

*Grounding is a claim-level supported fraction, not a binary check. A binary metric collapses to 0 under a strict judge — it measures the judge, not the model.*
