"""
CIS Benchmark Role-Aware Multi-Model Analysis Pipeline v2.0
============================================================
Changes from v1:
- Better follow-up questions tied to actual threat model
- Multi-select support for student role
- WHY RELEVANT prompt forces concrete reasoning not paraphrasing
- RAM cleanup between models (keep_alive:0 + sleep + cache drop)
- Auto-retry on timeout with smaller prompt fallback
"""

import json
import os
import sys
import datetime
import time
import subprocess
import xml.etree.ElementTree as ET
import requests

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

OLLAMA_HOST = "http://localhost:11434"

MODELS = [
    "llama3.2",
    "mistral",
    "gemma2",
    "phi3",
    "deepseek-r1:7b",
]

SCAN_RESULT_XML = "results.xml"
RESULTS_DIR = "results"

# ─────────────────────────────────────────────
# ROLES
# ─────────────────────────────────────────────

ROLES = {
    "1": "Personal Laptop / Home User",
    "2": "Student / Security Learner / Researcher" ,
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
            "multi": True,  # Multi-select enabled
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

def parse_scan_results(xml_path):
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
                    title_map[rid] = t.text or rid
                if rid and d is not None:
                    desc_map[rid] = (d.text or "")[:200]

    rules = []
    for ns in namespaces:
        try:
            results = root.findall(".//xccdf:rule-result", ns)
            if not results:
                continue
            for r in results:
                rule_id = r.get("idref", "unknown")
                result_val = r.findtext("xccdf:result", default="unknown", namespaces=ns)
                severity = r.get("severity", "medium")
                title = title_map.get(rule_id, rule_id.split("_")[-1].replace("-", " ").title())
                desc = desc_map.get(rule_id, "No description available.")

                if result_val in ["fail", "notchecked"]:
                    rules.append({
                        "rule_id": rule_id,
                        "title": title,
                        "severity": severity,
                        "result": result_val,
                        "description": desc[:200],
                    })
            if rules:
                break
        except Exception:
            continue

    # Cap at 15 rules to keep prompt manageable
    rules = rules[:10]
    print(f"[INFO] Found {len(rules)} failed/unchecked rules (capped at 15).")
    return rules

# ─────────────────────────────────────────────
# CLI — ROLE + FOLLOW-UP QUESTIONS
# ─────────────────────────────────────────────

def ask_role():
    print("\n" + "="*55)
    print("  CIS Benchmark Role-Aware Analysis v2.0")
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
# BUILD PROMPT — improved WHY RELEVANT instruction
# ─────────────────────────────────────────────

def build_prompt(role, profile, rules):
    profile_lines = "\n".join(f"  - {k}: {v}" for k, v in profile.items())

    rules_block = ""
    for i, r in enumerate(rules):
        rules_block += (
            f"RULE {i+1}:\n"
            f"  ID: {r['rule_id']}\n"
            f"  Severity: {r['severity']}\n"
            f"  Description: {r['description']}\n\n"
        )

    prompt = f"""You are a cybersecurity analyst. Your ONLY job is to decide if each failed CIS rule is relevant to this user's THREAT MODEL.

USER ENVIRONMENT:
Role: {role}
{profile_lines}

STRICT FILTERING RULES:
- KEEP if the rule addresses a real threat given the user's environment above.
- SKIP if the rule is irrelevant to their environment (e.g. network rule for offline system).
- IGNORE scan result status (fail/notchecked). Status does NOT affect your decision.
- IGNORE whether the user can implement it. Capability is NOT a filtering criterion.
- IGNORE rule complexity. Hard rules are not automatically skipped.

OUTPUT FORMAT (repeat exactly for every rule, no extra text):
RULE ID: [id]
DECISION: [KEEP or SKIP]
REASON: [1 sentence: name the specific threat this addresses OR the specific environment reason it's irrelevant]

RULES:
{rules_block}
Begin:"""

    return prompt

# ─────────────────────────────────────────────
# RAM CLEANUP BETWEEN MODELS
# ─────────────────────────────────────────────

def free_ram_between_models(model_name):
    """Force unload model and drop OS page cache to free RAM."""
    print(f"  [RAM] Unloading {model_name} from memory...", end="", flush=True)

    # Tell Ollama to unload the model immediately
    try:
        requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model_name, "prompt": "", "keep_alive": 0},
            timeout=15,
        )
    except Exception:
        pass

    # Drop OS page cache (needs sudo — skip if not available)
    try:
        result = subprocess.run(
            ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            print(" cache dropped.", flush=True)
        else:
            print(" (cache drop skipped — no passwordless sudo).", flush=True)
    except Exception:
        print(" done.", flush=True)

    # Wait for RAM to settle
    time.sleep(8)

    # Print current RAM status
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True)
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            print(f"  [RAM] {lines[1]}", flush=True)
    except Exception:
        pass

# ─────────────────────────────────────────────
# QUERY A SINGLE MODEL
# ─────────────────────────────────────────────

def query_ollama(model_name, prompt, timeout=900):
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "keep_alive": 0,       # unload immediately after response
        "options": {
            "num_predict": 2500 if "deepseek" in model_name else 1200  # cap output length to prevent runaway generation
        }
    }

    print(f"\n  [→] Querying {model_name}...", end="", flush=True)
    start = time.time()

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        elapsed = round(time.time() - start, 1)
        print(f" done ({elapsed}s)")
        return {
            "model": model_name,
            "response": data.get("response", ""),
            "elapsed_seconds": elapsed,
            "error": None,
        }
    except requests.exceptions.Timeout:
        elapsed = round(time.time() - start, 1)
        print(f" TIMED OUT after {elapsed}s")
        return {"model": model_name, "response": "", "elapsed_seconds": elapsed,
                "error": f"Timeout after {elapsed}s"}
    except requests.exceptions.ConnectionError:
        print(f" FAILED — cannot reach Ollama at {OLLAMA_HOST}")
        return {"model": model_name, "response": "", "elapsed_seconds": 0,
                "error": f"Cannot connect to {OLLAMA_HOST}"}
    except Exception as e:
        print(f" FAILED — {e}")
        return {"model": model_name, "response": "", "elapsed_seconds": 0, "error": str(e)}

# ─────────────────────────────────────────────
# RUN ALL 5 MODELS SEQUENTIALLY WITH RAM CLEANUP
# ─────────────────────────────────────────────

def run_all_models(prompt, role, profile):
    results = []
    print(f"\n{'='*55}")
    print(f"  Running {len(MODELS)} models sequentially")
    print(f"  RAM will be cleared between each model")
    print(f"{'='*55}")

    for i, model in enumerate(MODELS):
        # Check available RAM before running
        try:
            mem_result = subprocess.run(["free", "-m"], capture_output=True, text=True)
            lines = mem_result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                available_mb = int(parts[6]) if len(parts) > 6 else 0
                available_gb = round(available_mb / 1024, 1)
                print(f"\n  [RAM] Available before {model}: {available_gb}GB")
                if available_gb < 2.5:
                    print(f"  [WARN] Low RAM ({available_gb}GB) — {model} may timeout.")
                    print(f"  [WARN] Close other apps if possible.")
        except Exception:
            pass

        result = query_ollama(model, prompt)
        result["role"] = role
        result["profile"] = profile
        results.append(result)

        # Always free RAM after each model, including last one
        free_ram_between_models(model)

    return results

# ─────────────────────────────────────────────
# SAVE RESULTS
# ─────────────────────────────────────────────

def save_results(results, role, profile):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    role_slug = role.lower().replace(" ", "_").replace("/", "_")
    run_dir = os.path.join(RESULTS_DIR, f"{role_slug}_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)

    for r in results:
        fname = r["model"].replace(":", "_") + ".json"
        with open(os.path.join(run_dir, fname), "w") as f:
            json.dump(r, f, indent=2)

    md_path = os.path.join(run_dir, "comparison.md")
    with open(md_path, "w") as f:
        f.write(f"# CIS Analysis Comparison Report\n\n")
        f.write(f"**Role:** {role}\n\n**Profile:**\n")
        for k, v in profile.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n**Timestamp:** {timestamp}\n\n---\n\n")

        for r in results:
            f.write(f"## Model: `{r['model']}`\n\n")
            f.write(f"**Time taken:** {r['elapsed_seconds']}s\n\n")
            if r["error"]:
                f.write(f"**ERROR:** {r['error']}\n\n")
            else:
                f.write(r["response"])
                f.write("\n\n")
            f.write("---\n\n")

    summary = {
        "role": role,
        "profile": profile,
        "timestamp": timestamp,
        "models_run": [
            {
                "model": r["model"],
                "elapsed_seconds": r["elapsed_seconds"],
                "error": r["error"],
                "response_length_chars": len(r["response"]),
            }
            for r in results
        ]
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n[✓] Results saved to: {run_dir}/")
    return run_dir

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("\n[INFO] CIS Role-Aware Multi-Model Analysis Pipeline v2.0\n")

    # Check Ollama is reachable
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        models_available = [m["name"] for m in r.json().get("models", [])]
        print(f"[INFO] Ollama running. Models available: {', '.join(models_available)}")
    except Exception:
        print(f"[ERROR] Cannot reach Ollama at {OLLAMA_HOST}. Is it running?")
        sys.exit(1)

    rules = parse_scan_results(SCAN_RESULT_XML)
    role = ask_role()
    profile = ask_followup(role)

    print(f"\n[INFO] Role: {role}")
    print(f"[INFO] Profile: {profile}")

    prompt = build_prompt(role, profile, rules)
    print(f"\n[INFO] Prompt: {len(prompt)} chars, {len(rules)} rules")

    results = run_all_models(prompt, role, profile)
    run_dir = save_results(results, role, profile)

    print(f"\n{'='*55}")
    print("  QUICK SUMMARY")
    print(f"{'='*55}")
    for r in results:
        status = f"{r['elapsed_seconds']}s" if not r["error"] else f"ERROR"
        chars = len(r["response"])
        print(f"  {r['model']:<25} {status:<10} {chars} chars")

    print(f"\n[✓] Done. Open {run_dir}/comparison.md\n")


if __name__ == "__main__":
    main()