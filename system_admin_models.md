# System / Cloud Administrator — Models (keep/skip)

Profiles vary (public cloud, internal networks, VMs). Below: concise model keep/skip counts per run (timestamp).

- `google/gemma-4-31b-it`
  - 20260628_032937: kept 10 / skip 0
  - 20260628_115038: kept 10 / skip 0
  - 20260628_132511: kept 14 / skip 1
  - 20260628_204529: kept 10 / skip 0
  - 20260629_002507: kept 10 / skip 0
  - 20260629_010239: kept 11 / skip 4
  - 20260629_012211: kept 6 / skip 0
  - 20260629_013739: kept 5 / skip 0
  - 20260629_020422: kept 4 / skip 1

- `z-ai/glm-5.1`
  - variety of runs: kept values range 0–9, skip values range 0–4 (see timestamps)

- `moonshotai/kimi-k2.6`
  - runs show kept values 1–9 in different timestamps; often small kept counts

- `openai/gpt-oss-120b`, `deepseek-ai/deepseek-v4-pro`, `qwen/qwen3.5-397b-a17b`, `meta/llama-3.3-70b-instruct`
  - generally present in many runs with mostly kept 0 / skip 0 (exceptions and rate/errors exist)

- `mistralai/mistral-large-3-675b-instruct-2512`
  - multiple runs late-June show:
    - 20260630_211640: kept 10 / skip 5 (batches_used 3 of 4)
    - 20260630_211720: kept 8 / skip 7
    - 20260630_211854: kept 10 / skip 5
    - 20260629_020422: kept 3 / skip 2 (one run)

Notes: I prioritized model names and compact keep/skip per-timestamp entries. Say the word if you want me to add simple aggregated totals per model.
