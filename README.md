# Automated CIS Benchmark Remediation using an LLM Pipeline

**Author:** Pranjal Garg (IIIT Hyderabad)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![OpenSCAP](https://img.shields.io/badge/OpenSCAP-Compliant-green.svg)](https://www.open-scap.org/)
[![Ubuntu 24.04 | 22.04](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2022.04-orange.svg)](https://ubuntu.com/)

---

## 📌 Problem Statement

Hardening Linux operating systems against Center for Internet Security (CIS) Benchmarks traditionally relies heavily on context-blind vulnerability scanning and manual, unscalable human intervention.

* **The Context Problem:** OpenSCAP flags security issues, but it cannot evaluate whether an issue actually matters for a specific machine. Since a public production server and a personal laptop have vastly different security requirements, a human security expert must manually review every single failing rule by hand.
* **The Target Gap:** A baseline scan yields ~62% compliance score. Blindly applying OpenSCAP's default remediation scripts raises this to ~89%. This project targets the remaining **~11%**—the complex, context-dependent rules where blind automation fails and intelligent, context-aware decision-making is required.

---

## 💡 Proposed Solution & Architecture

This project offloads context-aware hardening to an automated, role-aware LLM pipeline:

1. **Context-Aware Rule Filtering:** Given an OpenSCAP scan result (`agent-test.xml`) and a system role description (e.g., MIN for personal laptops vs. MAX for cloud servers), the pipeline decides which rules to **KEEP** and remediate, and which to safely **SKIP**.
2. **Multi-Step Self-Correcting Agent:** Rather than relying on single-pass proposals, the agent operates in an iterative propose-apply-rescan loop (up to 3 attempts). If a proposed fix fails, verbatim terminal/OpenSCAP error output is fed back to the LLM for self-correction.
3. **Safety & Guardrails:**
   * **Sudoers Corruption Guard:** Monitors `/etc/sudoers.d` edits with `visudo -c` and automatically rolls back malformed configurations to prevent admin lockout.
   * **Human-in-the-Loop Escalation:** Pauses and asks the user for input during contextual ambiguities (multiple-choice questions), privileged credential requests, or high-risk system changes (firewall, SSH access).

---

## 🤖 Candidate LLMs Evaluated & Selection Process

Eight candidate open-weights models were hosted locally and offline on a lab server to evaluate classification accuracy, script generation capability, cost, and latency without internet dependency.

### 1. Complete List of Evaluated Models:
* 🔹 **DeepSeek-R1 (7B)** *(Local)*
* 🔹 **Gemma 2 (latest)** *(Local)*
* 🔹 **GPT-OSS (latest)** *(Local)*
* 🔹 **Granite 4.1 (8B)** *(Local)*
* 🔹 **Llama 3.2 (latest)** *(Local)*
* 🔹 **Mistral (latest)** *(Local)*
* 🔹 **Phi-3 (latest)** *(Local)*
* 🔹 **Qwen 2.5 (7B)** *(Local)*

### 2. Selection Pipeline:
1. **Initial Screening (Phase 1):** All **8 models** were tasked with classifying 63 CIS failing rules as KEEP or SKIP against the MIN and MAX ground truths to eliminate weak performers.
2. **One-Pass Remediation Test (Phase 2):** The **top 5 shortlisted models** (*Qwen 2.5 7B, Gemma 2, GPT-OSS, Mistral, Granite 4.1 8B*) were tasked with generating actual bash remediation scripts.
3. **Final Model Choice:** **Qwen 2.5 (7B)** emerged as the best-performing model across classification and script generation, leading the final multi-step production agent.

---

## 🛠 Tools & Technologies Used

* **Candidate Language Models:** Qwen 2.5 (7B), Gemma 2, GPT-OSS, Granite 4.1 (8B), Mistral, Llama 3.2, Phi-3, DeepSeek-R1 (7B)
* **Local Offline Hosting & Inference:** Ollama, OpenAI-compatible proxy endpoints running on local lab server
* **Vulnerability Scanning & Compliance:** OpenSCAP (`oscap` CLI), XCCDF / OVAL security standards
* **Core Language & Automation:** Python 3.10+, Bash scripting
* **Data Processing & Libraries:** `openpyxl` (Excel ground truth parsing), `xml.etree.ElementTree`, `requests`, `urllib3`
* **Operating Systems Tested:** Ubuntu 24.04 LTS (Noble Numbat), Ubuntu 22.04 LTS (Jammy Jellyfish)

---

## 📊 Performance & Evaluation Report

For full quantitative metrics, classification accuracy tables across all 8 models, wall-clock/CPU execution timing, one-pass remediation success rates, and Ubuntu 22.04 cross-validation details, please refer to the project report:

📄 **[View Full Project Report](file:///home/pranjal-garg/agentic-openscap-remediation/summer_project_report-3.pdf)**

---

## 🔮 Future Work

* **Cross-Distribution Testing:** Expanding evaluation and testing across a wider variety of Linux distributions (RHEL, Debian, Fedora) and diverse enterprise server roles.
* **Complex Script Synthesis:** Enhancing model prompts and hint databases to help the agent write better remediation scripts for persistent edge-case rule failures.
* **Policy & Safety Enforcement:** Strengthening system safety guardrails to ensure critical human-approval gates for high-risk system changes can never be bypassed.

---

## 📂 Repository Structure

```
.
├── analysis/                          # Classification & model screening scripts
│   ├── ubuntu_2204/
│   │   └── analysis_2204.py          # Screening for Ubuntu 22.04 Developer profile
│   └── ubuntu_2404/
│       ├── analysis.py               # Single-model screening runner
│       ├── analysisv2.py             # Multi-model comparative screening
│       ├── analysisv3.py             # Ground truth loader & evaluator
│       └── analysisv4.py             # Statistical summary & timing reporter
│
├── remediation/                       # Remediation agent execution scripts
│   ├── ubuntu_2204/
│   │   ├── remediation_2204_v1.py    # Single-pass remediation for Ubuntu 22.04
│   │   └── remediation_2204_v2.py    # Multi-pass remediation agent for 22.04
│   ├── ubuntu_2404/
│   │   ├── remediation_v1.py         # OpenSCAP Remediation Pipeline v1.3
│   │   ├── remediation_v2.py         # Multi-model pipeline v3.1
│   │   ├── remediation_v3.py         # Multi-pass retry agent with feedback (v3.3)
│   │   ├── remediation_v4.py         # Enhanced hints & timeout handling
│   │   └── remediation_final.py      # Production multi-pass remediation agent
│   └── version2/
│       ├── remediationv2.py
│       └── remediationv8.py
│
├── summer_project_report-3.pdf        # Full project report document
├── agent-test.xml                     # Ubuntu 24.04 OpenSCAP scan results
├── agent-test-22.xml                  # Ubuntu 22.04 OpenSCAP scan results
├── CIS_Ground_Truth_FULL_MAX_MIN.xlsx # Primary Ubuntu 24.04 ground truth mapping
├── CIS_Ground_Truth_FINAL.xlsx        # Ground truth reference sheet
└── remediation_results/              # Output JSON logs & execution reports
```

---

## 🚀 Getting Started

### Prerequisites

* Python 3.10+
* OpenSCAP CLI (`oscap`) installed on target host
* Access to a local LLM endpoint (e.g. Qwen 2.5 7B, Gemma 2, etc.)
* Python dependencies:
  ```bash
  pip install requests openpyxl urllib3
  ```

### Usage Examples

1. **Run Full Interactive Remediation (Ubuntu 24.04)**:
   ```bash
   python3 remediation/ubuntu_2404/remediation_final.py
   ```

2. **Run Autonomous Auto-Approve Mode with Qwen 2.5**:
   ```bash
   python3 remediation/ubuntu_2404/remediation_final.py --model qwen2.5:7b --profile MAX --auto
   ```

3. **Run MIN-to-MAX Delta Pass**:
   ```bash
   python3 remediation/ubuntu_2404/remediation_final.py --profile MAX --only-min-delta --auto
   ```

4. **Retry Failed Rules from Previous Run**:
   ```bash
   python3 remediation/ubuntu_2404/remediation_final.py --profile MIN --retry-failed remediation_results/qwen2.5_7b_MIN.json --auto
   ```

---

