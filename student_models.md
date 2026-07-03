# Student / Security Learner / Researcher — Models (keep/skip)

Profiles vary (learning workloads and technical depth). Below are concise model keep/skip counts per run (timestamp).

- `openai/gpt-oss-120b`
  - 20260627_204952: kept 0 / skip 0
  - 20260627_223626: kept 0 / skip 0
  - 20260627_235021: kept 5 / skip 0
  - 20260628_013459: kept 0 / skip 0
  - 20260628_021000: kept 0 / skip 0
  - 20260628_022237: kept 8 / skip 2

- `deepseek-ai/deepseek-v4-pro`
  - present in many runs; often kept 0 / skip 0 (some runs show rate limits)

- `google/gemma-4-31b-it`
  - 20260627_204952: kept 13 / skip 2
  - 20260627_223626: kept 9 / skip 1
  - 20260627_235021: kept 12 / skip 3
  - 20260628_013459: kept 9 / skip 1
  - 20260628_021000: kept 12 / skip 3
  - 20260628_022237: kept 9 / skip 1
  - 20260629_140602: (mistral-only run)
  - 20260630_180413/180603/180659/180746/211953: later runs include mistralai entries (see below)

- `z-ai/glm-5.1`
  - various runs: kept values range 0–6, skip values range 0–7 (see individual timestamps above)

- `qwen/qwen3.5-397b-a17b`
  - mostly kept 0 / skip 0 across student runs; occasional 1 kept in some runs

- `moonshotai/kimi-k2.6`
  - small kept numbers in some runs (1–2), other runs 0

- `mistralai/mistral-large-3-675b-instruct-2512` (student-only / later runs)
  - 20260629_140602: error (429)
  - 20260630_180603: kept 10 / skip 10
  - 20260630_180659: kept 8 / skip 7
  - 20260630_180746: kept 2 / skip 3
  - 20260630_211953: kept 3 / skip 2

Notes: I focused on model names and keep/skip counts per timestamp, kept details minimal. I can compute totals next if you want aggregated summaries.
