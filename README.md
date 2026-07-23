# Automated CIS Benchmark Remediation using an LLM Pipeline

**Author:** Pranjal Garg (IIIT Hyderabad)

---

## Environment Setup & How to Run Experiments

This project requires an Ubuntu virtual machine environment, OpenSCAP tools, and access to a local or remote LLM endpoint hosting candidate models. Follow the steps below to configure your environment and run the experiments.

### 1. Prerequisites and Virtual Machine Setup

* **Operating System:** Ubuntu 24.04 LTS or Ubuntu 22.04 LTS running inside a Virtual Machine (VirtualBox, VMware, or KVM).
* **Permissions:** A user account with passwordless `sudo` privileges (NOPASSWD) configured in `/etc/sudoers` so automated remediation scripts can execute system configuration commands without hanging on password prompts.
* **Python Environment:** Python 3.10 or higher.

#### Package Installation
Run the following commands on your Ubuntu VM to install system tools and OpenSCAP binaries:

```bash
sudo apt-get update
sudo apt-get install -y openscap-utils scap-security-guide python3-pip python3-venv git
```

#### Python Environment Configuration
Set up a dedicated virtual environment in the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install requests openpyxl urllib3
```

---

### 2. LLM Endpoint Configuration

The pipeline queries an OpenAI-compatible or Ollama LLM endpoint hosting local open-weights models (e.g., Qwen 2.5 7B, Gemma 2, Mistral, etc.). Ensure your LLM server is accessible from the VM.

Set the endpoint environment variables or edit the script configuration directly:

```bash
export LAB_URL="https://<YOUR_LLM_SERVER_IP>:<PORT>"
export LAB_USER="<YOUR_USERNAME>"
export LAB_PASS="<YOUR_PASSWORD>"
```

---

### 3. Required Code Configuration (Variables & Lines to Change)

Before executing scripts in `analysis/` or `remediation/`, update the configuration variables at the top of each script to match your local setup:

#### Key Variables in Scripts (e.g. `remediation/ubuntu_2404/remediation_final.py`):

| Variable Name | Typical Line Range | Description / Required Modification |
| :--- | :--- | :--- |
| `LAB_URL` | Lines 121-123 | Set to your LLM endpoint URL (e.g. `"https://10.1.96.96:8443"` or via env var). |
| `LAB_USER` & `LAB_PASS` | Lines 122-123 | Set basic authentication credentials for your LLM proxy. |
| `MODELS` | Lines 125-131 | List of model names to run (e.g. `["qwen2.5:7b"]`, `["gemma2:latest"]`, `["gpt-oss:latest"]`). |
| `SCAN_RESULT_XML` | Line 133 | Path to input OpenSCAP scan result XML (`"agent-test.xml"`). |
| `GROUND_TRUTH_XLSX` | Line 134 | Path to Ground Truth Excel file (`"CIS_Ground_Truth_FULL_MAX_MIN.xlsx"`). |
| `BENCHMARK_XML` | Lines 137-138 | Path to the SSG benchmark datastream XML on your host (e.g. `"~/Downloads/scap-security-guide-0.1.76/ssg-ubuntu2404-ds.xml"`). |
| `RESULTS_DIR` | Line 136 | Directory where output JSON result files will be saved (`"remediation_results"`). |
| `SYSTEM_INFO` | Lines 432-437 | Update dictionary to reflect your host (`hostname`, `kernel`, `os`, `arch`). |

---

### 4. Step-by-Step Experiment Execution

#### Step 1: Generate OpenSCAP Scan Result (Optional / Initial Scan)
To generate a baseline scan XML on your VM using OpenSCAP:

```bash
oscap xccdf eval \
  --profile xccdf_org.ssgproject.content_profile_cis \
  --results agent-test.xml \
  /usr/share/xml/scap/ssg/content/ssg-ubuntu2404-ds.xml
```

#### Step 2: Run Classification & Model Screening Analysis
To evaluate model classification accuracy (KEEP vs. SKIP decisions) against ground truth:

```bash
# Ubuntu 24.04 classification analysis
python3 analysis/ubuntu_2404/analysis.py

# Ubuntu 22.04 classification analysis
python3 analysis/ubuntu_2204/analysis_2204.py
```

#### Step 3: Run Interactive or Autonomous Remediation Agent

```bash
# Interactive mode (prompts for approval on KEEP rules)
python3 remediation/ubuntu_2404/remediation_final.py

# Autonomous auto-approve mode with Qwen 2.5 (7B) against MAX profile
python3 remediation/ubuntu_2404/remediation_final.py --model qwen2.5:7b --profile MAX --auto

# MIN-to-MAX delta pass (only test rules skipped under MIN profile)
python3 remediation/ubuntu_2404/remediation_final.py --profile MAX --only-min-delta --auto

# Retry rules that failed in a previous run
python3 remediation/ubuntu_2404/remediation_final.py --profile MIN --retry-failed remediation_results/qwen2.5_7b_MIN.json --auto
```

---

## Problem Statement

Hardening Linux operating systems against Center for Internet Security (CIS) Benchmarks traditionally relies heavily on context-blind vulnerability scanning and manual, unscalable human intervention.

* **The Context Problem:** OpenSCAP flags security issues, but it cannot evaluate whether an issue actually matters for a specific machine. Since a public production server and a personal laptop have vastly different security requirements, a human security expert must manually review every single failing rule by hand.
* **The Target Gap:** A baseline scan yields ~62% compliance score. Blindly applying OpenSCAP's default remediation scripts raises this to ~89%. This project targets the remaining **~11%**—the complex, context-dependent rules where blind automation fails and intelligent, context-aware decision-making is required.

---

## Proposed Solution & Architecture

This project offloads context-aware hardening to an automated, role-aware LLM pipeline:

1. **Context-Aware Rule Filtering:** Given an OpenSCAP scan result (`agent-test.xml`) and a system role description (e.g., MIN for personal laptops vs. MAX for cloud servers), the pipeline decides which rules to **KEEP** and remediate, and which to safely **SKIP**.
2. **Multi-Step Self-Correcting Agent:** Rather than relying on single-pass proposals, the agent operates in an iterative propose-apply-rescan loop (up to 3 attempts). If a proposed fix fails, verbatim terminal/OpenSCAP error output is fed back to the LLM for self-correction.
3. **Safety & Guardrails:**
   * **Sudoers Corruption Guard:** Monitors `/etc/sudoers.d` edits with `visudo -c` and automatically rolls back malformed configurations to prevent admin lockout.
   * **Human-in-the-Loop Escalation:** Pauses and asks the user for input during contextual ambiguities (multiple-choice questions), privileged credential requests, or high-risk system changes (firewall, SSH access).

---

## Candidate LLMs Evaluated & Selection Process

Eight candidate open-weights models were hosted locally and offline on a lab server to evaluate classification accuracy, script generation capability, cost, and latency without internet dependency.

### Complete List of Evaluated Models:
* DeepSeek-R1 (7B) (Local)
* Gemma 2 (latest) (Local)
* GPT-OSS (latest) (Local)
* Granite 4.1 (8B) (Local)
* Llama 3.2 (latest) (Local)
* Mistral (latest) (Local)
* Phi-3 (latest) (Local)
* Qwen 2.5 (7B) (Local)

### Selection Pipeline:
1. **Initial Screening (Phase 1):** All **8 models** were tasked with classifying 63 CIS failing rules as KEEP or SKIP against the MIN and MAX ground truths to eliminate weak performers.
2. **One-Pass Remediation Test (Phase 2):** The **top 5 shortlisted models** (*Qwen 2.5 7B, Gemma 2, GPT-OSS, Mistral, Granite 4.1 8B*) were tasked with generating actual bash remediation scripts.
3. **Final Model Choice:** **Qwen 2.5 (7B)** emerged as the best-performing model across classification and script generation, leading the final multi-step production agent.

---

## Tools & Technologies Used

* **Candidate Language Models:** Qwen 2.5 (7B), Gemma 2, GPT-OSS, Granite 4.1 (8B), Mistral, Llama 3.2, Phi-3, DeepSeek-R1 (7B)
* **Local Offline Hosting & Inference:** Ollama, OpenAI-compatible proxy endpoints running on local lab server
* **Vulnerability Scanning & Compliance:** OpenSCAP (`oscap` CLI), XCCDF / OVAL security standards
* **Core Language & Automation:** Python 3.10+, Bash scripting
* **Data Processing & Libraries:** `openpyxl` (Excel ground truth parsing), `xml.etree.ElementTree`, `requests`, `urllib3`
* **Operating Systems Tested:** Ubuntu 24.04 LTS (Noble Numbat), Ubuntu 22.04 LTS (Jammy Jellyfish)

---

## Performance & Evaluation Report

For full quantitative metrics, classification accuracy tables across all 8 models, wall-clock/CPU execution timing, one-pass remediation success rates, and Ubuntu 22.04 cross-validation details, please refer to the project report:

**[View Full Project Report](file:///home/pranjal-garg/agentic-openscap-remediation/summer_project_report-4.pdf)**

---

## Future Work

* **Cross-Distribution Testing:** Expanding evaluation and testing across a wider variety of Linux distributions (RHEL, Debian, Fedora) and diverse enterprise server roles.
* **Complex Script Synthesis:** Enhancing model prompts and hint databases to help the agent write better remediation scripts for persistent edge-case rule failures.
* **Policy & Safety Enforcement:** Strengthening system safety guardrails to ensure critical human-approval gates for high-risk system changes can never be bypassed.

---

## Repository Structure

The codebase is organized into two primary workflow directories—`analysis/` for model screening & evaluation, and `remediation/` for multi-pass remediation agent execution:

```
.
├── agent-test-22.xml
├── agent-test.xml
├── analysis
│   ├── ubuntu_2204
│   │   └── analysis_2204.py
│   └── ubuntu_2404
│       ├── analysis.py
│       ├── analysisv2.py
│       ├── analysisv3.py
│       └── analysisv4.py
├── break_rules.sh
├── CIS_Ground_Truth_FINAL.xlsx
├── CIS_Ground_Truth_FULL_MAX_MIN.xlsx
├── personal_laptop_models.md
├── README.md
├── remediation
│   ├── ubuntu_2204
│   │   ├── remediation_2204_v1.py
│   │   └── remediation_2204_v2.py
│   ├── ubuntu_2404
│   │   ├── remediation_final.py
│   │   ├── remediation_v1.py
│   │   ├── remediation_v2.py
│   │   ├── remediation_v3.py
│   │   └── remediation_v4.py
│   └── version2
│       ├── remediationv2.py
│       └── remediationv8.py
├── remediation_results
├── results
├── nvidia_model_results
├── results.xml
├── scan_results.xml
├── software_developer_models.md
├── software_developer_models_raw.md
├── student_models.md
├── summer_project_report-4.pdf
├── system_admin_models.md
└── version2
    ├── agent-test.xml
    ├── break_rules.sh
    ├── CIS_Ground_Truth_FINAL.xlsx
    └── CIS_Ground_Truth_FULL_MAX_MIN.xlsx
```

---

## License & Citation

This project was developed at **IIIT Hyderabad**. If you use this work, codebase, or ground-truth dataset in your research, please cite:

```bibtex
@misc{garg2026agenticopenscap,
  author = {Pranjal Garg},
  title = {Automated CIS Benchmark Remediation using an LLM Pipeline},
  institution = {IIIT Hyderabad},
  year = {2026}
}
```
