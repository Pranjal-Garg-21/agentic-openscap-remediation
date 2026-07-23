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
import threading
import xml.etree.ElementTree as ET
import requests
import urllib3

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False
    import resource  # stdlib fallback, Unix-only, coarser granularity

# The lab Ollama proxy uses a self-signed cert (VPN-internal), so we skip
# verification for that host — this silences the resulting urllib3 warning.
# NVIDIA's endpoint still gets normal cert verification.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# RESOURCE METRICS (wall time / peak RAM / CPU)
# ─────────────────────────────────────────────
# IMPORTANT CAVEAT: model inference runs remotely (NVIDIA's cloud, or the lab
# Ollama box over VPN). These metrics measure THIS SCRIPT's own client-side
# footprint (making HTTP calls, parsing text) — not the model server's GPU/CPU
# usage. Wall time is still meaningful (it's real end-to-end latency per
# model), but "peak RAM"/"CPU%" here describe the orchestration process, not
# the model's compute cost. To measure the lab box itself, instrument it
# directly (e.g. `ollama ps`, nvidia-smi, or a monitoring agent on that host).

class ResourceSampler:
    """
    Background sampler that polls this process's RSS memory and CPU% at a
    fixed interval while a model is being queried, so we can report a true
    peak (not just a before/after snapshot that could miss a spike).
    Falls back to a single before/after delta via the stdlib `resource`
    module if psutil isn't installed (coarser: only gives cumulative CPU
    time and lifetime-peak RSS, not a windowed peak).
    """
    def __init__(self, interval=0.2):
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None
        self.peak_rss_mb = 0.0
        self.cpu_samples = []
        self._process = psutil.Process(os.getpid()) if HAVE_PSUTIL else None
        self._start_ru = None

    def _poll_loop(self):
        self._process.cpu_percent(interval=None)  # prime the internal counter
        while not self._stop_event.is_set():
            try:
                rss_mb = self._process.memory_info().rss / (1024 * 1024)
                self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
                self.cpu_samples.append(self._process.cpu_percent(interval=None))
            except Exception:
                pass
            self._stop_event.wait(self.interval)

    def start(self):
        if HAVE_PSUTIL:
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
        else:
            self._start_ru = resource.getrusage(resource.RUSAGE_SELF)

    def stop(self):
        """Returns dict: peak_ram_mb, avg_cpu_percent, method."""
        if HAVE_PSUTIL:
            self._stop_event.set()
            if self._thread:
                self._thread.join(timeout=self.interval * 2)
            avg_cpu = round(sum(self.cpu_samples) / len(self.cpu_samples), 1) if self.cpu_samples else 0.0
            return {
                "peak_ram_mb": round(self.peak_rss_mb, 1),
                "avg_cpu_percent": avg_cpu,
                "method": "psutil (sampled every %.1fs)" % self.interval,
            }
        else:
            end_ru = resource.getrusage(resource.RUSAGE_SELF)
            cpu_time = (end_ru.ru_utime - self._start_ru.ru_utime) + \
                       (end_ru.ru_stime - self._start_ru.ru_stime)
            # ru_maxrss is KB on Linux, bytes on macOS — assume Linux (lab box is Ubuntu)
            return {
                "peak_ram_mb": round(end_ru.ru_maxrss / 1024, 1),
                "avg_cpu_percent": None,  # not derivable without psutil
                "cpu_time_seconds": round(cpu_time, 2),
                "method": "resource module fallback (install psutil for live sampling: pip install psutil --break-system-packages)",
            }

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# Set this in your shell instead of hardcoding it: export NVIDIA_API_KEY="nvapi-..."
NVIDIA_API_KEY = "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"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Lab Ollama server, reached over VPN via a basic-auth HTTPS proxy.
# Same rotation advice as the NVIDIA key above: prefer env vars over hardcoding
# once this is off your local machine, since this password is now in your
# chat history and this file. export LAB_URL / LAB_USER / LAB_PASS to override.
LAB_URL = os.environ.get("LAB_URL")
LAB_USER = os.environ.get("LAB_USER")
LAB_PASS = os.environ.get("LAB_PASS")

# Exact model strings straight from the NVIDIA NIM catalog pages, plus
# whatever's pulled on the lab Ollama box. Each entry is tagged with which
# backend serves it so the same pipeline can query both.
# Exact model strings pulled from your local Ollama box.
# All entries are mapped to the 'lab' backend to query your Ollama server.
MODELS = [
    {"backend": "lab", "name": "qwen2.5:7b"},
    # {"backend": "lab", "name": "gpt-oss:latest"},
    # {"backend": "lab", "name": "granite4.1:8b"},
    # {"backend": "lab", "name": "phi3:latest"},
    # {"backend": "lab", "name": "gemma2:latest"},
    # {"backend": "lab", "name": "mistral:latest"},
    # {"backend": "lab", "name": "llama3.2:latest"},
    # {"backend": "lab", "name": "deepseek-r1:7b"},
    # {"backend": "lab", "name": "mistral-small:latest"},  # Added this from the available list
]

# 1. Point to your 22.04 scan file
def resolve_path(p):
    if not p or os.path.isabs(p): return p
    if os.path.exists(p): return os.path.abspath(p)
    s_dir = os.path.dirname(os.path.abspath(__file__))
    for base in [s_dir, os.path.join(s_dir, ".."), os.path.join(s_dir, "..", "..")]:
        cand = os.path.abspath(os.path.join(base, p))
        if os.path.exists(cand): return cand
    return os.path.abspath(os.path.join(s_dir, "..", "..", p))

SCAN_RESULT_XML = "agent-test-22.xml"  

# 2. Reflect the target OS being evaluated
SYSTEM_INFO = {
    "hostname": "ubuntu2204-scap-test",
    "kernel": "5.15.0-generic",
    "os": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
    "arch": "x86_64",
}
RESULTS_DIR = "results"

# Max chars for rule descriptions to optimize cloud payload context footprint
DESCRIPTION_MAX_CHARS = 200

# Batching logic — 40 target rules / 20 per batch = 2 rounds
BATCH_SIZE = 1

# KEEP_TARGET is the early-stop threshold used in v2 (stop once N rules are
# KEPT). Set to None here so every rule in TARGET_RULE_IDS gets a verdict
# across both batches instead of stopping early.
KEEP_TARGET = None

# ─────────────────────────────────────────────
# TARGET RULE IDS — restrict analysis to just these 40 rules
# ─────────────────────────────────────────────
TARGET_RULE_IDS = [
    # === System and Software Integrity (2 rules) ===
    "xccdf_org.ssgproject.content_rule_aide_build_database",
    "xccdf_org.ssgproject.content_rule_partition_for_tmp",

    # === Sudo Restrictions (3 rules) ===
    "xccdf_org.ssgproject.content_rule_sudo_custom_logfile",
    "xccdf_org.ssgproject.content_rule_sudo_remove_no_authenticate",
    "xccdf_org.ssgproject.content_rule_sudo_require_reauthentication",

    # === Authentication & PAM Policies (10 rules) ===
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_unix_authtok",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_deny",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_enabled",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_unlock_time",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_dcredit",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_minlen",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_ucredit",
    "xccdf_org.ssgproject.content_rule_set_password_hashing_algorithm_systemauth",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_unix_no_remember",
    "xccdf_org.ssgproject.content_rule_no_empty_passwords_unix",

    # === Shell Environment & Session Timeouts (3 rules) ===
    "xccdf_org.ssgproject.content_rule_accounts_umask_etc_bashrc",
    "xccdf_org.ssgproject.content_rule_accounts_umask_etc_login_defs",
    "xccdf_org.ssgproject.content_rule_accounts_tmout",

    # === Bootloader & AppArmor Controls (3 rules) ===
    "xccdf_org.ssgproject.content_rule_grub2_enable_apparmor",
    "xccdf_org.ssgproject.content_rule_grub2_uefi_password",
    "xccdf_org.ssgproject.content_rule_service_systemd-journal-upload_enabled",

    # === Journald Logging Configurations (5 rules) ===
    "xccdf_org.ssgproject.content_rule_journald_compress",
    "xccdf_org.ssgproject.content_rule_journald_forward_to_syslog",
    "xccdf_org.ssgproject.content_rule_journald_storage",
    "xccdf_org.ssgproject.content_rule_systemd_journal_upload_server_tls",
    "xccdf_org.ssgproject.content_rule_systemd_journal_upload_url",

    # === Networking & Kernel Sysctl Flags (7 rules) ===
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv6_conf_all_forwarding",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_accept_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_log_martians",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_rp_filter",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_tcp_syncookies",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_send_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_ip_forward",

    # === Firewalls & Core System Shadow Files (3 rules) ===
    "xccdf_org.ssgproject.content_rule_service_nftables_enabled",
    "xccdf_org.ssgproject.content_rule_firewall_single_service_active",
    "xccdf_org.ssgproject.content_rule_file_groupowner_backup_etc_gshadow",

    # === File System Kernel Modules (4 rules) ===
    "xccdf_org.ssgproject.content_rule_kernel_module_cramfs_disabled",
    "xccdf_org.ssgproject.content_rule_kernel_module_hfs_disabled",
    "xccdf_org.ssgproject.content_rule_kernel_module_hfsplus_disabled",
    "xccdf_org.ssgproject.content_rule_kernel_module_jffs2_disabled",

    # === Shared Memory Mount Tweaks (3 rules) ===
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nodev",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_noexec",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nosuid",

    # === Core Dump Bounds & Address Randomization (3 rules) ===
    "xccdf_org.ssgproject.content_rule_disable_users_coredumps",
    "xccdf_org.ssgproject.content_rule_sysctl_fs_suid_dumpable",
    "xccdf_org.ssgproject.content_rule_sysctl_kernel_randomize_va_space",

    # === Cron Directory Permissions & File Owners (6 rules) ===
    "xccdf_org.ssgproject.content_rule_file_groupowner_backup_etc_gshadow",
    "xccdf_org.ssgproject.content_rule_file_groupowner_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_owner_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_d",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_daily",
    "xccdf_org.ssgproject.content_rule_file_permissions_crontab",

    # === Non-compliant Server/Client Packages & Services (11 rules) ===
    "xccdf_org.ssgproject.content_rule_package_nis_removed",
    "xccdf_org.ssgproject.content_rule_package_vsftpd_removed",
    "xccdf_org.ssgproject.content_rule_service_vsftpd_disabled",
    "xccdf_org.ssgproject.content_rule_package_ftp_removed",
    "xccdf_org.ssgproject.content_rule_package_tnftp_removed",
    "xccdf_org.ssgproject.content_rule_package_openldap-clients_removed",
    "xccdf_org.ssgproject.content_rule_package_rpcbind_removed",
    "xccdf_org.ssgproject.content_rule_package_ypserv_removed",
    "xccdf_org.ssgproject.content_rule_package_telnet_removed",
    "xccdf_org.ssgproject.content_rule_package_rsync_removed",
    "xccdf_org.ssgproject.content_rule_service_rsyncd_disabled"
]


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

    prompt = f"""You are an expert cybersecurity analyst evaluating CIS benchmark rules against a target threat model.

MY SYSTEM CONFIGURATION:
{system_lines}

USER ENVIRONMENT & ROLE:
Role: {role}
{profile_lines}

CRITICAL FILTERING POSTURE (STRICT RULES):
1. KEEP the rule if it addresses a real, theoretical risk to this OS/kernel or environment.
2. IGNORE whether the user has the technical capability to implement it.
3. IGNORE rule implementation complexity. Even if a rule is incredibly difficult or disruptive, do not automatically skip it.
4. Focus purely on whether the underlying vulnerability applies to this system architecture and user profile.

RULE TO EVALUATE:
{rules_block}

Provide your analysis exactly in this plain-text format:
RULE ID: <rule_id>
DECISION: <KEEP or SKIP>
REASON: <one short sentence explanation balancing the strict criteria above>"""

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
                "fatal": False,
            }
            
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code

            # 404/410 mean the model string is wrong or retired on NVIDIA's
            # side — retrying won't help, so bail out of this model entirely.
            if status in (404, 410):
                elapsed = round(time.time() - start, 1)
                err_msg = f"HTTP Error: {status} - {e.response.text[:150]}"
                return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                        "error": err_msg, "fatal": True}

            # If it's a server timeout (502, 504) and we haven't run out of retries, wait and try again
            if status in [502, 504] and attempt < max_retries - 1:
                print(" [API Busy - Retrying in 5s]...", end="", flush=True)
                time.sleep(5)
                continue
                
            elapsed = round(time.time() - start, 1)
            err_msg = f"HTTP Error: {status} - {e.response.text[:100]}"
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                    "error": err_msg, "fatal": False}
            
        except Exception as e:
            elapsed = round(time.time() - start, 1)
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                    "error": str(e), "fatal": False}


# ─────────────────────────────────────────────
# QUERY THE LAB OLLAMA SERVER (VPN, basic auth, self-signed cert)
# ─────────────────────────────────────────────

def get_lab_models():
    """
    GET /models on the lab proxy. Returns a list of model name strings.
    Handles a few likely response shapes since the proxy's exact schema
    hasn't been confirmed yet — a plain list, an Ollama-style {"models":[...]}
    with "name" keys, or a {"models": ["name", ...]} list of strings.
    """
    try:
        resp = requests.get(
            f"{LAB_URL}/models",
            auth=(LAB_USER, LAB_PASS),
            verify=False,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] Could not reach lab server at {LAB_URL}/models: {e}")
        print("        Check that you're on the VPN and the address/port are correct.")
        return []

    if isinstance(data, list):
        return [m.get("name", m) if isinstance(m, dict) else m for m in data]
    if isinstance(data, dict) and "models" in data:
        return [m.get("name", m) if isinstance(m, dict) else m for m in data["models"]]

    print(f"[WARN] Unrecognized /models response shape, raw: {str(data)[:300]}")
    return []


def query_lab_model(model_name, prompt, batch_len=20, timeout=900):
    """
    POST /chat on the lab proxy, same call contract as query_nvidia_nim so
    both backends can share run_one_model_through_batches unchanged.
    """
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        # Ollama's default num_predict can be small (as low as 128 on some
        # versions) and would silently truncate a 20-rule batch response.
        # This key is ignored harmlessly if the proxy doesn't pass it through.
        "stream": False,
        "options": {"num_predict": min(8192, 200 * max(batch_len, 1) + 300)},
    }

    start = time.time()
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{LAB_URL}/chat",
                auth=(LAB_USER, LAB_PASS),
                json=payload,
                verify=False,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Defensive extraction — exact shape not yet confirmed against the
            # live server. Covers Ollama-native, OpenAI-proxy, and plain-text.
            reply_text = ""
            if isinstance(data, dict):
                if "message" in data and isinstance(data["message"], dict):
                    reply_text = data["message"].get("content", "")
                elif "choices" in data:
                    reply_text = data["choices"][0]["message"].get("content", "")
                elif "response" in data:
                    reply_text = data["response"]

            elapsed = round(time.time() - start, 1)
            return {
                "model": model_name,
                "response": reply_text,
                "elapsed_seconds": elapsed,
                "error": None,
                "fatal": False,
            }

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code

            # 401 = bad credentials, 404 = model not pulled on the lab box.
            # Neither improves on retry.
            if status in (401, 404):
                elapsed = round(time.time() - start, 1)
                err_msg = f"HTTP Error: {status} - {e.response.text[:150]}"
                return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                        "error": err_msg, "fatal": True}

            if status in (500, 502, 503, 504) and attempt < max_retries - 1:
                print(f" [Lab server busy/{status} - retrying in 5s]...", end="", flush=True)
                time.sleep(5)
                continue

            elapsed = round(time.time() - start, 1)
            err_msg = f"HTTP Error: {status} - {e.response.text[:150]}"
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                    "error": err_msg, "fatal": False}

        except requests.exceptions.ConnectionError as e:
            # Most commonly: not on the VPN, or the box is off.
            if attempt < max_retries - 1:
                print(" [Lab server unreachable - retrying in 5s, check VPN]...", end="", flush=True)
                time.sleep(5)
                continue
            elapsed = round(time.time() - start, 1)
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                    "error": f"Connection failed (check VPN): {e}", "fatal": True}

        except Exception as e:
            elapsed = round(time.time() - start, 1)
            return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                    "error": str(e), "fatal": False}


def query_model(model_entry, prompt, batch_len=20, timeout=900):
    """Dispatches to the right backend based on model_entry['backend']."""
    backend = model_entry["backend"]
    name = model_entry["name"]
    if backend == "nim":
        return query_nvidia_nim(name, prompt, batch_len=batch_len, timeout=timeout)
    elif backend == "lab":
        return query_lab_model(name, prompt, batch_len=batch_len, timeout=timeout)
    else:
        raise ValueError(f"Unknown backend '{backend}' for model {name}")
# ─────────────────────────────────────────────
# RUN MODEL WITH CLEAR TERMINAL OUTPUT
# ─────────────────────────────────────────────

def run_one_model_through_batches(model_entry, batches, role, profile, total_rules):
    model = model_entry["name"]
    backend = model_entry["backend"]
    print(f"\n{'='*60}")
    print(f"  EVALUATING MODEL: {model}  [{backend}]")
    print(f"{'='*60}")

    sampler = ResourceSampler()
    wall_start = time.perf_counter()
    sampler.start()

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
        
        source = "Cloud" if backend == "nim" else "Lab Server"
        print(f"  [Batch {batch_num}/{len(batches)}] Evaluating rules {start_idx} to {end_idx} via {source}...", end="", flush=True)
        
        prompt = build_prompt(role, profile, batch)
        call_result = query_model(model_entry, prompt, batch_len=len(batch), timeout=180)

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

        if call_result.get("fatal"):
            print(f"  [FATAL] Model '{model}' unavailable (bad name, retired, not pulled, "
                  f"or auth/VPN issue). Skipping remaining batches for this model.")
            break

        if KEEP_TARGET is not None and len(kept_rules) >= KEEP_TARGET:
            print(f"  [INFO] Reached {KEEP_TARGET} KEEPs. Stopping early.")
            break

    wall_time = round(time.perf_counter() - wall_start, 2)
    resource_metrics = sampler.stop()

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

    cpu_display = f"{resource_metrics['avg_cpu_percent']}%" if resource_metrics.get("avg_cpu_percent") is not None \
        else f"{resource_metrics.get('cpu_time_seconds', '?')}s CPU time"
    print(f"\n  [METRICS] Wall time: {wall_time}s | Client peak RAM: {resource_metrics['peak_ram_mb']} MB | "
          f"Client CPU: {cpu_display}  (script-side only, not model-server-side — see note in code)")

    return {
        "model": model,
        "backend": backend,
        "role": role,
        "profile": profile,
        "batches_used": batches_used,
        "total_batches_available": len(batches),
        "kept_rules": kept_rules,
        "all_decisions": all_decisions,
        "batch_calls": batch_calls,
        "total_elapsed_seconds": round(sum(b["elapsed_seconds"] for b in batch_calls), 1),
        "wall_time_seconds": wall_time,
        "resource_metrics": resource_metrics,
        "error": batch_calls[-1]["error"] if batch_calls and all(b["error"] for b in batch_calls) else None,
    }

def run_all_models(rules, role, profile):
    batches = batch_rules(rules, BATCH_SIZE)
    total_rules = len(rules)

    nim_count = sum(1 for m in MODELS if m["backend"] == "nim")
    lab_count = sum(1 for m in MODELS if m["backend"] == "lab")

    print(f"\n{'='*60}")
    print(f"  STARTING RUN: {len(MODELS)} models ({nim_count} NIM cloud, {lab_count} lab)")
    print(f"  Rules: {total_rules} ({len(batches)} batches of {BATCH_SIZE})")
    print(f"{'='*60}")

    if lab_count > 0:
        available = get_lab_models()
        if available:
            wanted = {m["name"] for m in MODELS if m["backend"] == "lab"}
            missing = wanted - set(available)
            if missing:
                print(f"[WARN] These lab models aren't in the server's /models list: {sorted(missing)}")
                print(f"       Available on lab server: {available}")

    results = []
    for model_entry in MODELS:
        result = run_one_model_through_batches(model_entry, batches, role, profile, total_rules)
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

            rm = r.get("resource_metrics", {})
            cpu_str = f"{rm.get('avg_cpu_percent')}%" if rm.get("avg_cpu_percent") is not None \
                else f"{rm.get('cpu_time_seconds', '?')}s CPU time"
            f.write(f"**Wall time:** {r.get('wall_time_seconds', '?')}s | "
                    f"**Client peak RAM:** {rm.get('peak_ram_mb', '?')} MB | "
                    f"**Client CPU:** {cpu_str} "
                    f"_(script-side only — model inference runs remotely, see note in source)_\n\n")

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
                "backend": r.get("backend"),
                "batches_used": r["batches_used"],
                "total_batches_available": r["total_batches_available"],
                "kept_count": len(r["kept_rules"]),
                "skip_count": sum(1 for d in r["all_decisions"].values() if d["decision"] == "SKIP"),
                "unparsed_count": sum(1 for d in r["all_decisions"].values() if d["decision"] == "UNPARSED"),
                "total_elapsed_seconds": r["total_elapsed_seconds"],
                "wall_time_seconds": r.get("wall_time_seconds"),
                "resource_metrics": r.get("resource_metrics"),
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
        rm = r.get("resource_metrics", {})
        cpu_str = f"{rm.get('avg_cpu_percent')}%" if rm.get("avg_cpu_percent") is not None \
            else f"{rm.get('cpu_time_seconds', '?')}s"
        print(f"  {r['model']:<32} {status:<10} KEEP {len(r['kept_rules'])}/{keep_denom}  "
              f"(batches {r['batches_used']}/{r['total_batches_available']})  "
              f"wall={r.get('wall_time_seconds', '?')}s  peakRAM={rm.get('peak_ram_mb', '?')}MB  cpu={cpu_str}")

    if not HAVE_PSUTIL:
        print("\n  [NOTE] psutil not installed — CPU%/peak RAM used the coarser 'resource' module "
              "fallback (cumulative process lifetime figures, not per-model windowed peaks).")
        print("         For accurate per-model sampling: pip install psutil --break-system-packages")

    print(f"\n[✓] Done. Check terminal output above or open {run_dir}/comparison.md for deep dive.\n")

if __name__ == "__main__":
    main()