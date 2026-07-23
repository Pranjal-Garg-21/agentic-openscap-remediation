#!/usr/bin/env python3
"""
CIS Benchmark Role-Aware Cloud Multi-Model Analysis Pipeline v5.0 (v3 script)
==============================================================================
Evaluates frontier-grade models on NVIDIA NIM cloud infrastructure.
Handles context-heavy OpenSCAP descriptions with zero local hardware load.

This variant restricts analysis to a fixed TARGET_RULE_IDS list (40 rules)
instead of every failed rule in the scan, and runs them in 2 batches of 20
with no early-stop KEEP target — every rule gets a KEEP/SKIP verdict.
"""

import json
import os
import re
import sys
import datetime
import time
import xml.etree.ElementTree as ET
import requests

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Set this in your shell instead of hardcoding it: export NVIDIA_API_KEY="nvapi-..."
NVIDIA_API_KEY = "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"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Exact model strings straight from the NVIDIA NIM catalog pages
MODELS = [
    
    "openai/gpt-oss-120b",
    "deepseek-ai/deepseek-v4-pro",
    "google/gemma-4-31b-it",
    "z-ai/glm-5.2",
    "qwen/qwen3.5-397b-a17b",
    "moonshotai/kimi-k2.6",
    "meta/llama-3.3-70b-instruct",
    "mistralai/mistral-large-3-675b-instruct-2512",
# "microsoft/phi-4-mini-instruct",
]

def resolve_path(p):
    if not p or os.path.isabs(p): return p
    if os.path.exists(p): return os.path.abspath(p)
    s_dir = os.path.dirname(os.path.abspath(__file__))
    for base in [s_dir, os.path.join(s_dir, ".."), os.path.join(s_dir, "..", "..")]:
        cand = os.path.abspath(os.path.join(base, p))
        if os.path.exists(cand): return cand
    return os.path.abspath(os.path.join(s_dir, "..", "..", p))

SCAN_RESULT_XML = "agent-test.xml"
RESULTS_DIR = "results"

# Max chars for rule descriptions to optimize cloud payload context footprint
DESCRIPTION_MAX_CHARS = 1200

# Batching logic — 40 target rules / 20 per batch = 2 rounds
BATCH_SIZE = 5

# KEEP_TARGET is the early-stop threshold used in v2 (stop once N rules are
# KEPT). Set to None here so every rule in TARGET_RULE_IDS gets a verdict
# across both batches instead of stopping early.
KEEP_TARGET = None

# ─────────────────────────────────────────────
# TARGET RULE IDS — restrict analysis to just these 40 rules
# ─────────────────────────────────────────────
TARGET_RULE_IDS = [
    # === PAM / Password Policy (8 rules) ===
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_deny",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_enabled",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_unlock_time",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_minlen",  # Fixed upstream name
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_ucredit",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_dcredit",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_unix_no_remember",  # Replaced with modern Ubuntu variant
    "xccdf_org.ssgproject.content_rule_no_empty_passwords_unix",  # Corrected to specific Ubuntu identifier

    # === Kernel Parameters / Sysctl (8 rules) ===
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_send_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_accept_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_log_martians",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_rp_filter",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_tcp_syncookies",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_ip_forward",
    "xccdf_org.ssgproject.content_rule_sysctl_kernel_randomize_va_space",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv6_conf_all_forwarding",

    # === System Settings (6 rules) ===
    "xccdf_org.ssgproject.content_rule_sysctl_fs_suid_dumpable",  # Modern kernel core dump block variant
    "xccdf_org.ssgproject.content_rule_disable_users_coredumps",  # Active Ubuntu user space core dump block
    "xccdf_org.ssgproject.content_rule_accounts_tmout",
    "xccdf_org.ssgproject.content_rule_accounts_umask_etc_bashrc",
    "xccdf_org.ssgproject.content_rule_sudo_require_reauthentication",
    "xccdf_org.ssgproject.content_rule_sudo_custom_logfile",  # Modern Ubuntu log file rule mapping

    # === AppArmor (3 rules) ===
    "xccdf_org.ssgproject.content_rule_package_apparmor-utils_installed",  # Fixed dash mismatch
    "xccdf_org.ssgproject.content_rule_all_apparmor_profiles_in_enforce_complain_mode",
    "xccdf_org.ssgproject.content_rule_grub2_enable_apparmor",

    # === Unnecessary Packages (5 rules) ===
    "xccdf_org.ssgproject.content_rule_package_ftp_removed",
    "xccdf_org.ssgproject.content_rule_package_telnet_removed",
    "xccdf_org.ssgproject.content_rule_package_rsync_removed",
    "xccdf_org.ssgproject.content_rule_service_rsyncd_disabled",
    "xccdf_org.ssgproject.content_rule_package_openldap-clients_removed",  # Fixed dash mismatch

    # === Cron / File Permissions (4 rules) ===
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_d",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_daily",
    "xccdf_org.ssgproject.content_rule_file_owner_cron_allow",

    # === Filesystem Modules (3 rules) ===
    "xccdf_org.ssgproject.content_rule_kernel_module_cramfs_disabled",
    "xccdf_org.ssgproject.content_rule_kernel_module_hfs_disabled",
    "xccdf_org.ssgproject.content_rule_kernel_module_jffs2_disabled",

    # === /dev/shm Mount Options (3 rules) ===
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nodev",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_noexec",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nosuid"
]

# ─────────────────────────────────────────────
# HOST SYSTEM INFO
# ─────────────────────────────────────────────

SYSTEM_INFO = {
    "hostname": "pranjal-garg-IdeaPad-Slim-5-14IRL8",
    "kernel": "6.17.0-29-generic #29~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC Mon May 11 10:30:58 UTC 2",
    "os": "Ubuntu 24.04 LTS (Noble Numbat)",
    "arch": "x86_64",
}

# ─────────────────────────────────────────────
# ROLES
# ─────────────────────────────────────────────

ROLES = {
    "1": "Personal Laptop / Home User",
    "2": "Student / Security Learner / Researcher",
    "3": "Software Developer",
    "4": "System / Cloud Administrator",
}

FOLLOWUP_QUESTIONS = {
    "Personal Laptop / Home User": [
        {
            "q": "To help me assess the risk of local tampering, who physically uses this computer?",
            "options": {
                "1": "Just me (Low risk of physical tampering)",
                "2": "Shared with family or roommates (Moderate risk, needs basic user isolation)",
            },
            "key": "physical_access",
            "multi": False,
        },
        {
            "q": "To determine how aggressive the firewall/network rules should be, where do you connect?",
            "options": {
                "1": "Only trusted home/private networks (Standard firewall is fine)",
                "2": "Frequently on public campus or cafe Wi-Fi (Needs aggressive network hardening)",
            },
            "key": "network_environment",
            "multi": False,
        },
    ],
    "Student / Security Learner / Researcher": [
        {
            "q": "What do you actually use this computer for? (Select ALL that apply)",
            "options": {
                "1": "Coding & Development (Writing code, running local web servers, or building apps)",
                "2": "Security & Hacking (Playing with network scanners, testing vulnerabilities, or CTFs)",
                "3": "General Technical Work (Basic scripting, data analysis, and standard terminal usage)",
            },
            "key": "learning_workloads",
            "multi": True, 
        },
        {
            "q": "To tailor my explanations, how comfortable are you with the Linux terminal?",
            "options": {
                "1": "Beginner (Explain exactly what the commands do before I run them)",
                "2": "Advanced (Just give me the raw commands or config file edits, I know what they do)",
            },
            "key": "technical_depth",
            "multi": False,
        },
    ],
    "Software Developer": [
        {
            "q": "To avoid breaking your local environment, what are you building? (Select ALL that apply)",
            "options": {
                "1": "Web / Full-Stack (MERN, React Native, Node.js - needs local port access)",
                "2": "Systems / Low-Level (C/C++, Linux kernels - needs deep system execution rights)",
                "3": "Containerized Apps (Docker/Podman - relies on virtual networking)",
            },
            "key": "dev_stack",
            "multi": True,
        },
        {
            "q": "To set the right network security posture, does this machine accept external connections?",
            "options": {
                "1": "Yes, I run local servers/APIs that teammates or external tools connect to",
                "2": "No, it's strictly offline compiling and local-only testing",
            },
            "key": "network_exposure",
            "multi": False,
        },
    ],
    "System / Cloud Administrator": [
        {
            "q": "To ensure I generate safe remediation scripts, how sensitive is this server to downtime?",
            "options": {
                "1": "Production / Critical (Use extreme caution. Do not suggest live service restarts.)",
                "2": "Internal / Workstation (Standard caution. Brief, localized service restarts are acceptable.)",
                "3": "Ephemeral (Don't give me live bash commands. Just give me the config/Dockerfile fixes.)",
            },
            "key": "downtime_sensitivity",
            "multi": False,
        },
        {
            "q": "To understand the threat surface, where does this infrastructure live?",
            "options": {
                "1": "Public Cloud (AWS, GCP, etc. - highly exposed to internet scanning)",
                "2": "Internal Corporate Network (Behind a perimeter firewall)",
                "3": "Local Virtual Machine (Sandboxed environment)",
            },
            "key": "infrastructure_location",
            "multi": False,
        },
    ],
}

# ─────────────────────────────────────────────
# PARSE SCAN XML
# ─────────────────────────────────────────────

def clean_description_text(elem):
    if elem is None:
        return ""
    raw = " ".join(elem.itertext())
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw

def parse_scan_results(xml_path):
    """
    Builds the rule list for this run from TARGET_RULE_IDS.

    Unlike v2 (which only pulled rules with result == 'fail'), this pulls
    every rule in TARGET_RULE_IDS regardless of its pass/fail/notselected
    status in the scan — the 20 failing rules already analyzed are not
    touched here at all. We just need title/description/severity for the
    LLM KEEP/SKIP judgment call, not the scan verdict.
    """
    xml_path = resolve_path(xml_path)
    if not os.path.exists(xml_path):
        print(f"[ERROR] Scan file not found: {xml_path}")
        sys.exit(1)

    print(f"[INFO] Parsing {xml_path}...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    namespaces = [
        {"xccdf": "http://checklists.nist.gov/xccdf/1.2"},
        {"xccdf": "http://checklists.nist.gov/xccdf/1.1"},
    ]

    title_map = {}
    desc_map = {}
    for ns in namespaces:
        for rule in root.iter():
            if rule.tag.endswith("}Rule"):
                rid = rule.get("id", "")
                t = rule.find("xccdf:title", ns)
                d = rule.find("xccdf:description", ns)
                if rid and t is not None:
                    title_map[rid] = clean_description_text(t) or rid
                if rid and d is not None:
                    desc_map[rid] = clean_description_text(d)

    target_set = set(TARGET_RULE_IDS)
    result_map = {}   # rule_id -> (result, severity)
    for ns in namespaces:
        try:
            results = root.findall(".//xccdf:rule-result", ns)
            for r in results:
                rule_id = r.get("idref", "unknown")
                if rule_id not in target_set:
                    continue
                result_val = r.findtext("xccdf:result", default="unknown", namespaces=ns)
                severity = r.get("severity", "medium")
                result_map[rule_id] = (result_val, severity)
            if result_map:
                break
        except Exception:
            continue

    rules = []
    missing = []
    for rid in TARGET_RULE_IDS:
        title = title_map.get(rid, rid.replace("xccdf_org.ssgproject.content_rule_", "").replace("_", " ").title())
        desc = desc_map.get(rid, "No description available.")
        result_val, severity = result_map.get(rid, ("unknown", "medium"))

        if rid not in title_map and rid not in result_map:
            missing.append(rid)
            continue

        rules.append({
            "rule_id": rid,
            "title": title,
            "severity": severity,
            "result": result_val,
            "description": desc[:DESCRIPTION_MAX_CHARS],
        })

    print(f"[INFO] Pulled {len(rules)}/{len(TARGET_RULE_IDS)} target rules from scan XML "
          f"(pass/fail status ignored).")
    if missing:
        print(f"[WARN] {len(missing)} target rule(s) not found in this scan file at all "
              f"(no Rule definition, can't build a prompt for them):")
        for m in sorted(missing):
            print(f"    - {m}")

    return rules

def batch_rules(rules, batch_size=5):
    return [rules[i:i + batch_size] for i in range(0, len(rules), batch_size)]

# ─────────────────────────────────────────────
# CLI — ROLE + FOLLOW-UP QUESTIONS
# ─────────────────────────────────────────────

def ask_role():
    print("\n" + "="*55)
    print("  CIS Benchmark Role-Aware Cloud Analysis v4.0")
    print("="*55)
    print("\nSelect your role:\n")
    for k, v in ROLES.items():
        print(f"  [{k}] {v}")
    print()
    while True:
        choice = input("Enter number: ").strip()
        if choice in ROLES:
            return ROLES[choice]
        print("  Invalid. Try again.")

def ask_followup(role):
    questions = FOLLOWUP_QUESTIONS.get(role, [])
    profile = {}

    if questions:
        print(f"\n── Follow-up questions for {role} ──\n")

    for item in questions:
        print(f"  {item['q']}")
        for k, v in item["options"].items():
            print(f"    [{k}] {v}")

        if item.get("multi"):
            print("  Enter numbers separated by commas (e.g. 1,3):")
            while True:
                ans = input("  Your choices: ").strip()
                choices = [a.strip() for a in ans.split(",")]
                if all(c in item["options"] for c in choices):
                    profile[item["key"]] = ", ".join(item["options"][c] for c in choices)
                    break
                print("  Invalid. Try again.")
        else:
            while True:
                ans = input("  Your choice: ").strip()
                if ans in item["options"]:
                    profile[item["key"]] = item["options"][ans]
                    break
                print("  Invalid. Try again.")
        print()

    return profile

# ─────────────────────────────────────────────
# BUILD PROMPT
# ─────────────────────────────────────────────

def build_prompt(role, profile, rules):
    profile_lines = "\n".join(f"  - {k}: {v}" for k, v in profile.items())
    system_lines = "\n".join(f"  - {k}: {v}" for k, v in SYSTEM_INFO.items())

    rules_block = ""
    for i, r in enumerate(rules):
        rules_block += (
            f"RULE {i+1}:\n"
            f"  ID: {r['rule_id']}\n"
            f"  Title: {r['title']}\n"
            f"  Severity: {r['severity']}\n"
            f"  Description: {r['description']}\n\n"
        )

    prompt = f"""[SYSTEM INSTRUCTION: YOU ARE A PARSING MACHINE. DO NOT BE CONVERSATIONAL. DO NOT PROVIDE ANY INTRODUCTORY OR CONCLUDING TEXT. PROVIDE ONLY THE EXACT RULE-BY-RULE OUTPUT BLOCKS REQUESTED BELOW. ]
    You are a cybersecurity analyst. Your ONLY job is to decide if each failed CIS rule is relevant to this user's THREAT MODEL.

HOST SYSTEM:
{system_lines}

USER ENVIRONMENT:
Role: {role}
{profile_lines}

STRICT FILTERING RULES:
- KEEP if the rule addresses a real threat given the user's environment and host system above.
- SKIP if the rule is irrelevant to their environment (e.g. network rule for offline system) OR does not apply to this OS/kernel/architecture.
- IGNORE scan result status (fail). Status does NOT affect your decision.
- IGNORE whether the user can implement it. Capability is NOT a filtering criterion.
- IGNORE rule complexity. Hard rules are not automatically skipped.
- Use the rule's full description below (not just the title) to judge what the rule actually does before deciding.
Your response shhould include rule id, decision (KEEP or SKIP), and a brief reason for your decision.
If possible keep the output format as a structured list of RULE ID, DECISION, and REASON for each rule.

RULES:
{rules_block}
Begin:"""

    return prompt

# ─────────────────────────────────────────────
# PARSE MODEL RESPONSE
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# PARSE MODEL RESPONSE
# ─────────────────────────────────────────────

_DECISION_BLOCK_RE = re.compile(
    r"RULE\s*ID:\s*(?P<rule_id>\S+)\s*"
    r"DECISION:\s*(?P<decision>KEEP|SKIP)\s*"
    r"REASON:\s*(?P<reason>.+?)(?=RULE\s*ID:|\Z)",
    re.IGNORECASE | re.DOTALL,
)

def parse_model_decisions(response_text):
    # 1. Clean the text of standard AI clutter
    text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE)
    text = text.replace("**", "").replace("*", "").replace("`", "")
    
    decisions = {}
    
    # 2. Use a "loose" regex that finds any pattern looking like RULE ID: xxxx
    # and captures text until the next RULE ID
    pattern = re.compile(
        r"(?:RULE|Rule|rule)\s*(?:ID|id)?[:\-\s]+(?P<rule_id>\S+)\s*"
        r"(?:DECISION|Decision|decision)[:\-\s]+(?P<decision>KEEP|SKIP)\s*"
        r"(?:REASON|Reason|reason)[:\-\s]+(?P<reason>.+?)(?=RULE\s*ID:|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    
    for match in pattern.finditer(text):
        rid = match.group("rule_id").strip()
        decision = match.group("decision").strip().upper()
        reason = re.sub(r"\s+", " ", match.group("reason")).strip()
        decisions[rid] = {"decision": decision, "reason": reason}
        
    return decisions
# ─────────────────────────────────────────────
# QUERY A SINGLE MODEL VIA NVIDIA NIM CLOUD
# ─────────────────────────────────────────────

def query_nvidia_nim(model_name, prompt, timeout=900):
    if NVIDIA_API_KEY == "nvapi-YOUR-API-KEY-HERE":
        print("\n[ERROR] Replace 'nvapi-YOUR-API-KEY-HERE' with your real NVIDIA API Key at the top of the file.")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    max_tokens = 2200 if "deepseek" in model_name or "kimi" in model_name else 1500
    
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "top_p": 0.95,
        "stream": False
    }
    
    if any(k in model_name for k in ["deepseek", "kimi", "qwen"]):
        payload["chat_template_kwargs"] = {"enable_thinking": True}

    start = time.time()
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            response = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            
            message_data = data["choices"][0]["message"]
            reply_text = message_data.get("content") or message_data.get("reasoning_content") or ""
            
            elapsed = round(time.time() - start, 1)
            return {
                "model": model_name,
                "response": reply_text,
                "elapsed_seconds": elapsed,
                "error": None,
            }
            
        except requests.exceptions.HTTPError as e:
            # If it's a server timeout (502, 504) and we haven't run out of retries, wait and try again
            if e.response.status_code in [502, 504] and attempt < max_retries - 1:
                print(" [API Busy - Retrying in 5s]...", end="", flush=True)
                time.sleep(5)
                continue
                
            elapsed = round(time.time() - start, 1)
            err_msg = f"HTTP Error: {e.response.status_code} - {e.response.text[:100]}"
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed, "error": err_msg}
            
        except Exception as e:
            elapsed = round(time.time() - start, 1)
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed, "error": str(e)}
# ─────────────────────────────────────────────
# RUN MODEL WITH CLEAR TERMINAL OUTPUT
# ─────────────────────────────────────────────

def run_one_model_through_batches(model, batches, role, profile, total_rules):
    print(f"\n{'='*60}")
    print(f"  EVALUATING MODEL: {model}")
    print(f"{'='*60}")
    
    kept_rules = {}
    all_decisions = {}
    batch_calls = []
    batches_used = 0

    for batch_num, batch in enumerate(batches, start=1):
        if KEEP_TARGET is not None and len(kept_rules) >= KEEP_TARGET:
            break

        batches_used = batch_num
        start_idx = (batch_num - 1) * BATCH_SIZE + 1
        end_idx = min(batch_num * BATCH_SIZE, total_rules)
        
        print(f"  [Batch {batch_num}/{len(batches)}] Evaluating rules {start_idx} to {end_idx} via Cloud...", end="", flush=True)
        
        prompt = build_prompt(role, profile, batch)
        call_result = query_nvidia_nim(model, prompt)

        if call_result["error"]:
            print(f" ERROR: {call_result['error']}")
        else:
            print(f" done ({call_result['elapsed_seconds']}s)")
            
        parsed = {}
        if not call_result["error"]:
            parsed = parse_model_decisions(call_result["response"])
            if not parsed:
                print(f"  [WARN] Output format failed to parse for this batch.")

        for rule in batch:
            rid = rule["rule_id"]
            decision_entry = parsed.get(rid)
            
            if decision_entry is None:
                short = rid.replace("xccdf_org.ssgproject.content_rule_", "")
                for k, v in parsed.items():
                    if short in k or k in short:
                        decision_entry = v
                        break
                        
            if decision_entry is None:
                decision_entry = {"decision": "UNPARSED", "reason": "No parseable decision."}

            all_decisions[rid] = decision_entry
            if decision_entry["decision"] == "KEEP" and rid not in kept_rules:
                kept_rules[rid] = {
                    **decision_entry,
                    "title": rule["title"],
                    "severity": rule["severity"],
                }

        batch_calls.append({
            "batch_num": batch_num,
            "rule_ids_in_batch": [r["rule_id"] for r in batch],
            "raw_response": call_result["response"],
            "elapsed_seconds": call_result["elapsed_seconds"],
            "error": call_result["error"],
            "kept_count_after_batch": len(kept_rules),
        })

        if KEEP_TARGET is not None and len(kept_rules) >= KEEP_TARGET:
            print(f"  [INFO] Reached {KEEP_TARGET} KEEPs. Stopping early.")
            break

    print(f"\n  FINAL KEEP ({len(kept_rules)}):")
    if not kept_rules:
        print("    (None)")
    for rid, info in kept_rules.items():
        short_id = rid.replace('xccdf_org.ssgproject.content_rule_', '')
        snippet = (info['reason'][:60] + '...') if len(info['reason']) > 60 else info['reason']
        print(f"    + {short_id}\n      └─ {snippet}")
        
    skipped_count = sum(1 for d in all_decisions.values() if d["decision"] == "SKIP")
    print(f"\n  FINAL SKIP ({skipped_count}):")
    if skipped_count == 0:
        print("    (None)")
    for rid, info in all_decisions.items():
        if info["decision"] == "SKIP":
            print(f"    - {rid.replace('xccdf_org.ssgproject.content_rule_', '')}")

    return {
        "model": model,
        "role": role,
        "profile": profile,
        "batches_used": batches_used,
        "total_batches_available": len(batches),
        "kept_rules": kept_rules,
        "all_decisions": all_decisions,
        "batch_calls": batch_calls,
        "total_elapsed_seconds": round(sum(b["elapsed_seconds"] for b in batch_calls), 1),
        "error": batch_calls[-1]["error"] if batch_calls and all(b["error"] for b in batch_calls) else None,
    }

def run_all_models(rules, role, profile):
    batches = batch_rules(rules, BATCH_SIZE)
    total_rules = len(rules)
    
    print(f"\n{'='*60}")
    print(f"  STARTING RUN: {len(MODELS)} Cloud Models")
    print(f"  Rules: {total_rules} ({len(batches)} batches of {BATCH_SIZE})")
    print(f"{'='*60}")

    results = []
    for model in MODELS:
        result = run_one_model_through_batches(model, batches, role, profile, total_rules)
        results.append(result)
        # Smooth window progression gap protecting against aggressive API bursts
        time.sleep(2)

    return results

# ─────────────────────────────────────────────
# SAVE RESULTS (Markdown + JSON)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# SAVE RESULTS (Markdown + JSON)
# ─────────────────────────────────────────────

def save_results(results, role, profile, total_rules_available):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    role_slug = role.lower().replace(" ", "_").replace("/", "_")
    run_dir = os.path.join(resolve_path(RESULTS_DIR), f"{role_slug}_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)

    for r in results:
        fname = r["model"].replace(":", "_").replace("/", "_") + ".json"
        with open(os.path.join(run_dir, fname), "w") as f:
            json.dump(r, f, indent=2)

    md_path = os.path.join(run_dir, "comparison.md")
    with open(md_path, "w") as f:
        f.write(f"# CIS Analysis Comparison Report\n\n")
        f.write(f"**Role:** {role}\n\n**Profile:**\n")
        for k, v in profile.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n**Total failed rules available:** {total_rules_available}\n")
        keep_target_label = KEEP_TARGET if KEEP_TARGET is not None else "None (full analysis, no early stop)"
        f.write(f"**Batch size:** {BATCH_SIZE} | **KEEP target:** {keep_target_label}\n")
        f.write(f"\n**Timestamp:** {timestamp}\n\n---\n\n")

        for r in results:
            n_keep = len(r["kept_rules"])
            n_skip = sum(1 for d in r["all_decisions"].values() if d["decision"] == "SKIP")
            n_unparsed = sum(1 for d in r["all_decisions"].values() if d["decision"] == "UNPARSED")
            f.write(f"## Model: `{r['model']}`\n\n")
            f.write(f"**Batches used:** {r['batches_used']}/{r['total_batches_available']} | ")
            f.write(f"**Total time:** {r['total_elapsed_seconds']}s\n\n")
            keep_denom = KEEP_TARGET if KEEP_TARGET is not None else len(r["all_decisions"]) or n_keep
            f.write(f"**KEEP: {n_keep}/{keep_denom}** | SKIP: {n_skip} | Unparsed: {n_unparsed}\n\n")

            if r["error"]:
                f.write(f"**ERROR:** {r['error']}\n\n")

            f.write("### Kept rules\n\n")
            if r["kept_rules"]:
                for rid, info in r["kept_rules"].items():
                    f.write(f"- **{info['title']}** (`{rid}`, severity: {info['severity']})\n")
                    f.write(f"  - Reason: {info['reason']}\n")
            else:
                f.write("_None kept._\n")

            f.write("\n### Raw batch responses\n\n")
            for bc in r["batch_calls"]:
                f.write(f"<details><summary>Batch {bc['batch_num']} ({bc['elapsed_seconds']}s, kept so far: {bc['kept_count_after_batch']})</summary>\n\n")
                if bc["error"]:
                    f.write(f"ERROR: {bc['error']}\n\n")
                else:
                    f.write("```\n" + str(bc["raw_response"]) + "\n```\n\n")
                f.write("</details>\n\n")

            f.write("---\n\n")

    summary = {
        "role": role,
        "profile": profile,
        "timestamp": timestamp,
        "total_rules_available": total_rules_available,
        "batch_size": BATCH_SIZE,
        "keep_target": KEEP_TARGET,
        "models_run": [
            {
                "model": r["model"],
                "batches_used": r["batches_used"],
                "total_batches_available": r["total_batches_available"],
                "kept_count": len(r["kept_rules"]),
                "skip_count": sum(1 for d in r["all_decisions"].values() if d["decision"] == "SKIP"),
                "unparsed_count": sum(1 for d in r["all_decisions"].values() if d["decision"] == "UNPARSED"),
                "total_elapsed_seconds": r["total_elapsed_seconds"],
                "error": r["error"],
            }
            for r in results
        ]
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    return run_dir

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("\n[INFO] CIS Role-Aware Multi-Model Cloud Analysis Pipeline v4.0\n")

    rules = parse_scan_results(SCAN_RESULT_XML)
    if not rules:
        print("[ERROR] No FAILED rules found in scan file. Nothing to analyze.")
        sys.exit(1)

    role = ask_role()
    profile = ask_followup(role)

    print(f"\n[INFO] Role: {role}")
    print(f"[INFO] Profile: {profile}")

    results = run_all_models(rules, role, profile)
    run_dir = save_results(results, role, profile, total_rules_available=len(rules))

    print(f"\n{'='*60}")
    print("  QUICK SUMMARY")
    print(f"{'='*60}")
    for r in results:
        status = f"{r['total_elapsed_seconds']}s" if not r["error"] else "ERROR"
        keep_denom = KEEP_TARGET if KEEP_TARGET is not None else len(r["all_decisions"])
        print(f"  {r['model']:<46} {status:<10} KEEP {len(r['kept_rules'])}/{keep_denom}  "
              f"(batches {r['batches_used']}/{r['total_batches_available']})")

    print(f"\n[✓] Done. Check terminal output above or open {run_dir}/comparison.md for deep dive.\n")

if __name__ == "__main__":
    main()