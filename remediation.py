cat > ~/openscap-agent/remediation.py << 'ENDOFFILE'
#!/usr/bin/env python3
"""
OpenSCAP Remediation Agent v1.2
Parses failing rules, queries local LLM with enriched context,
applies fixes with human approval, hallucination detection,
path verification, and self-correction loop.
"""

import xml.etree.ElementTree as ET
import subprocess
import requests
import os
import re
from datetime import datetime

OLLAMA_URL   = "http://10.0.2.2:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
RESULTS_FILE = "/home/pranjal-garg/agent-test.xml"
BENCHMARK    = "/home/pranjal-garg/Downloads/scap-security-guide-0.1.76/ssg-ubuntu2404-ds.xml"
LOG_FILE     = "/home/pranjal-garg/openscap-agent/agent-run.log"

KNOWN_EXCEPTIONS = [
    'systemd_journal_upload_server_tls',
    'systemd_journal_upload_url',
    'journald_disable_forward_to_syslog',
    'grub2_uefi_password',
    'partition_for_tmp'
]

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def get_failing_rules(xml_path):
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = 'http://checklists.nist.gov/xccdf/1.2'
        failing = []
        for rr in root.iter(f'{{{ns}}}rule-result'):
            result = rr.find(f'{{{ns}}}result')
            if result is not None and result.text == 'fail':
                failing.append({
                    'id': rr.get('idref', 'unknown'),
                    'severity': rr.get('severity', 'unknown'),
                })
        return failing
    except PermissionError:
        print(f"Permission denied reading {xml_path}")
        print("Run: sudo chmod 644 " + xml_path)
        exit(1)
    except FileNotFoundError:
        print(f"Results file not found: {xml_path}")
        exit(1)

def get_rule_context(rule_id):
    """Extract description and fix text from benchmark XML."""
    try:
        tree = ET.parse(BENCHMARK)
        root = tree.getroot()
        ns = 'http://checklists.nist.gov/xccdf/1.2'
        context = {'description': '', 'fixtext': '', 'check': ''}
        for rule in root.iter(f'{{{ns}}}Rule'):
            if rule.get('id') == rule_id:
                desc = rule.find(f'{{{ns}}}description')
                if desc is not None:
                    context['description'] = ''.join(
                        desc.itertext()).strip()[:600]
                fix = rule.find(f'{{{ns}}}fixtext')
                if fix is not None and fix.text:
                    context['fixtext'] = fix.text.strip()[:600]
                check = rule.find(f'.//{{{ns}}}check-content')
                if check is not None and check.text:
                    context['check'] = check.text.strip()[:400]
                break
    except Exception:
        pass
    return context

def get_system_state(rule_id):
    """Probe live system state relevant to the failing rule."""
    short = rule_id.replace('xccdf_org.ssgproject.content_rule_', '')
    state = {}
    if 'cron' in short:
        out, _ = run_command('ls -la /etc/cron* 2>/dev/null')
        state['cron_files'] = out[:300]
    elif 'ssh' in short:
        out, _ = run_command(
            'sudo grep -v "^#" /etc/ssh/sshd_config 2>/dev/null | grep -v "^$"')
        state['sshd_config'] = out[:400]
    elif 'journald' in short or 'journal' in short:
        out, _ = run_command('cat /etc/systemd/journald.conf 2>/dev/null')
        state['journald_conf'] = out[:300]
    elif 'postfix' in short:
        out, _ = run_command('postconf inet_interfaces 2>/dev/null')
        state['postfix'] = out[:200]
    elif 'apparmor' in short:
        out, _ = run_command('sudo apparmor_status 2>/dev/null | head -20')
        state['apparmor'] = out[:300]
    elif 'firewall' in short or 'nftables' in short or 'ufw' in short:
        out, _ = run_command(
            'sudo systemctl is-active ufw nftables iptables 2>/dev/null')
        state['firewall'] = out[:200]
    elif 'partition' in short or 'mount' in short:
        out, _ = run_command('mount | grep -E "/tmp|/var|/home"')
        state['mounts'] = out[:200]
    elif 'grub' in short or 'boot' in short:
        out, _ = run_command(
            'sudo grep -r "password\|superusers" /etc/grub.d/ 2>/dev/null | head -5')
        state['grub'] = out[:200]
    elif 'aide' in short:
        out, _ = run_command('systemctl is-active aidecheck.timer 2>/dev/null')
        state['aide_timer'] = out[:100]
    elif 'file_' in short or 'permission' in short or 'owner' in short:
        out, _ = run_command('ls -la /etc/cron.allow /etc/cron.deny 2>/dev/null')
        state['file_state'] = out[:200]
    return state

def ask_llm(rule_id, severity, context=None, system_state=None):
    short_name = rule_id.replace(
        'xccdf_org.ssgproject.content_rule_', ''
    ).replace('_', ' ')

    context_block = ""
    if context:
        if context.get('description'):
            context_block += f"\nRULE DESCRIPTION:\n{context['description']}\n"
        if context.get('fixtext'):
            context_block += f"\nOFFICIAL FIX TEXT FROM BENCHMARK:\n{context['fixtext']}\n"
        if context.get('check'):
            context_block += f"\nWHAT THE CHECK TESTS:\n{context['check']}\n"

    state_block = ""
    if system_state:
        state_block = "\nCURRENT SYSTEM STATE:\n"
        for k, v in system_state.items():
            state_block += f"{k}:\n{v}\n"

    prompt = f"""You are a Linux security expert fixing OpenSCAP CIS benchmark failures on Ubuntu 24.04.
Ubuntu 24.04 uses systemd, apt, ufw, and standard Debian file paths like /etc/ssh/sshd_config.
Do NOT invent commands or tools. Only use standard Ubuntu binaries: sed, awk, systemctl, apt, chmod, chown, tee.

FAILING RULE: {short_name}
RULE ID: {rule_id}
SEVERITY: {severity}
{context_block}{state_block}
Respond in EXACTLY this format, one field per line, no extra text:
EXPLANATION: (what this rule checks and why it matters, 1-2 sentences)
RISK_LEVEL: LOW or MEDIUM or HIGH
FIX_COMMAND: (one single bash command using only standard Ubuntu tools, or MANUAL if impossible to script)
VERIFY_COMMAND: (oscap single-rule eval command to verify)
CATEGORY: CONFIG_CHANGE or PACKAGE_INSTALL or SERVICE_CHANGE or INFRASTRUCTURE or BENCHMARK_CONFLICT or MANUAL_ONLY"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120
        )
        return response.json().get("response", "No response from LLM")
    except requests.exceptions.ConnectionError:
        return "ERROR: Cannot connect to Ollama."
    except requests.exceptions.Timeout:
        return "ERROR: LLM timed out."

def parse_llm_response(response):
    fields = {
        'EXPLANATION': '',
        'RISK_LEVEL': 'UNKNOWN',
        'FIX_COMMAND': 'MANUAL',
        'VERIFY_COMMAND': '',
        'CATEGORY': 'UNKNOWN'
    }
    for line in response.split('\n'):
        for key in fields:
            if line.startswith(f"{key}:"):
                fields[key] = line.split(':', 1)[1].strip()
    return fields

def run_command(cmd):
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True, timeout=60
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "Command timed out", 1

def command_exists(cmd):
    parts = cmd.strip().split()
    binary = parts[1] if parts[0] == 'sudo' and len(parts) > 1 else parts[0]
    check, _ = run_command(f"which {binary} 2>/dev/null")
    return bool(check.strip())

def verify_rule(rule_id):
    cmd = (
        f"sudo oscap xccdf eval "
        f"--rule {rule_id} "
        f"{BENCHMARK} 2>/dev/null | grep 'Result'"
    )
    output, _ = run_command(cmd)
    return 'pass' in output.lower()

def main():
    print("\n" + "="*60)
    print("  OpenSCAP Remediation Agent v1.2")
    print("  Model: llama3.2 @ 10.0.2.2:11434")
    print("  Benchmark: CIS Level 1 Workstation - Ubuntu 24.04")
    print("="*60 + "\n")
    log("Agent started")

    run_command(f"sudo chmod 644 {RESULTS_FILE} 2>/dev/null")
    failing = get_failing_rules(RESULTS_FILE)

    if not failing:
        print("No failing rules found!")
        log("No failing rules found")
        return

    log(f"Found {len(failing)} failing rules")
    print(f"Found {len(failing)} failing rules to analyze\n")

    results = {'fixed': [], 'skipped': [], 'failed': [], 'manual': []}

    for i, rule in enumerate(failing, 1):
        rule_id = rule['id']
        severity = rule['severity']
        short_id = rule_id.replace('xccdf_org.ssgproject.content_rule_', '')

        print(f"\n{'='*60}")
        print(f"[{i}/{len(failing)}] Analyzing rule...")
        print(f"  ID      : {rule_id}")
        print(f"  Severity: {severity}")

        # Check known exceptions first
        if any(exc in short_id for exc in KNOWN_EXCEPTIONS):
            print("\n  INFO: Known infrastructure exception.")
            print("  Reason: Requires enterprise infrastructure not available in lab.")
            log(f"KNOWN EXCEPTION: {rule_id}")
            results['manual'].append(rule_id)
            input("  Press Enter to continue to next rule...")
            continue

        # Gather enriched context before calling LLM
        print("\n  Extracting rule context from benchmark XML...")
        rule_ctx = get_rule_context(rule_id)
        if rule_ctx.get('description'):
            print(f"  Context found: {rule_ctx['description'][:80]}...")

        print("  Probing live system state...")
        sys_state = get_system_state(rule_id)
        if sys_state:
            print(f"  System state keys captured: {list(sys_state.keys())}")

        # Query LLM with full context
        print("  Querying local LLM for analysis...")
        raw_response = ask_llm(rule_id, severity, rule_ctx, sys_state)

        if raw_response.startswith("ERROR"):
            print(f"  {raw_response}")
            results['skipped'].append(rule_id)
            continue

        parsed = parse_llm_response(raw_response)

        # Path verification and self-correction loop
        extracted_paths = re.findall(r'(/[a-zA-Z0-9_\-\.\*]+)', parsed['FIX_COMMAND'])
        corrected_context = ""

        for path in extracted_paths:
            clean_path = path.replace('*', '').rstrip('/')
            if clean_path and not os.path.exists(clean_path):
                print(f"\n  [Path Check] Warning: '{clean_path}' does not exist!")
                base_name = os.path.basename(clean_path).replace('s', '')
                probe_cmd = f"find /etc /usr/share /var -name '*{base_name}*' -type f 2>/dev/null | head -n 1"
                probe_output, _ = run_command(probe_cmd)
                if probe_output.strip():
                    actual_path = probe_output.strip()
                    print(f"  [Path Check] Mapped to: {actual_path}")
                    corrected_context += f"Path '{path}' does not exist. Use '{actual_path}' instead.\n"
                else:
                    corrected_context += f"Path '{path}' is invalid on this system.\n"

        if corrected_context:
            print("\n  [Self-Correction] Re-querying LLM with corrected paths...")
            correction_prompt = f"""Previous fix command used invalid paths:
{corrected_context}
Rewrite the FIX_COMMAND using only the valid paths listed above."""
            raw_response = ask_llm(
                rule_id + "\nPATH_CORRECTION:\n" + correction_prompt,
                severity, rule_ctx, sys_state)
            parsed = parse_llm_response(raw_response)

        print(f"\n  EXPLANATION : {parsed['EXPLANATION']}")
        print(f"  RISK LEVEL  : {parsed['RISK_LEVEL']}")
        print(f"  CATEGORY    : {parsed['CATEGORY']}")
        print(f"  FIX COMMAND : {parsed['FIX_COMMAND']}")

        if parsed['FIX_COMMAND'] == 'MANUAL' or parsed['CATEGORY'] in [
            'INFRASTRUCTURE', 'BENCHMARK_CONFLICT', 'MANUAL_ONLY'
        ]:
            print("\n  This rule requires manual intervention.")
            log(f"MANUAL: {rule_id}")
            results['manual'].append(rule_id)
            input("  Press Enter to continue...")
            continue

        print(f"\n  Proposed fix:\n  $ {parsed['FIX_COMMAND']}\n")

        while True:
            choice = input(
                "  Apply this fix? [y=yes / n=skip / d=details] : "
            ).strip().lower()

            if choice == 'd':
                print(f"\n  Full LLM response:\n{raw_response}\n")
                continue

            elif choice == 'y':
                print("\n  Applying fix...")

                fix_cmd = parsed['FIX_COMMAND']
                if not fix_cmd.startswith('sudo'):
                    fix_cmd = 'sudo ' + fix_cmd

                if not command_exists(fix_cmd):
                    print(f"  Command not found - LLM may have hallucinated this.")
                    print(f"  Command was: {fix_cmd}")
                    log(f"HALLUCINATED COMMAND: {rule_id} | CMD: {fix_cmd}")
                    results['failed'].append(rule_id)
                    input("  Press Enter to continue...")
                    break

                log(f"APPLYING FIX: {rule_id} | CMD: {fix_cmd}")
                output, returncode = run_command(fix_cmd)

                if output:
                    print(f"  Output: {output[:200]}")

                if returncode == 0:
                    print("  Fix applied.")
                    print("  Verifying with oscap...")
                    if verify_rule(rule_id):
                        print("  PASS - Rule now passes!")
                        log(f"VERIFIED PASS: {rule_id}")
                        results['fixed'].append(rule_id)
                    else:
                        print("  FAIL - Rule still failing after fix.")
                        log(f"STILL FAILING: {rule_id}")
                        results['failed'].append(rule_id)
                else:
                    print(f"  Error: exit code {returncode}")
                    log(f"FIX ERROR: {rule_id} | exit code {returncode}")
                    results['failed'].append(rule_id)
                break

            elif choice == 'n':
                print("  Skipped.")
                log(f"SKIPPED: {rule_id}")
                results['skipped'].append(rule_id)
                break
            else:
                print("  Please enter y, n, or d")

    print("\n" + "="*60)
    print("  AGENT RUN COMPLETE - SUMMARY")
    print("="*60)
    print(f"  Fixed successfully : {len(results['fixed'])}")
    print(f"  Still failing      : {len(results['failed'])}")
    print(f"  Skipped by user    : {len(results['skipped'])}")
    print(f"  Manual exceptions  : {len(results['manual'])}")

    if results['fixed']:
        print("\n  Rules fixed:")
        for r in results['fixed']:
            print(f"    + {r}")
    if results['manual']:
        print("\n  Documented exceptions:")
        for r in results['manual']:
            print(f"    ! {r}")

    log(f"Agent complete. Fixed:{len(results['fixed'])} "
        f"Failed:{len(results['failed'])} "
        f"Skipped:{len(results['skipped'])} "
        f"Manual:{len(results['manual'])}")
    print(f"\n  Log saved to: {LOG_FILE}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
ENDOFFILE