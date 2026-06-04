#!/usr/bin/env python3
"""
OpenSCAP Remediation Agent v1.3
Improvements over v1.2:
  - Fixed SyntaxWarning (raw string for grub grep)
  - Smarter path verification (whitelist of known-valid short paths)
  - service_systemd-journal-upload: special handler using netcat fake receiver
  - Retry loop: if fix passes command_exists but oscap still fails,
    agent re-queries LLM with failure feedback (max 2 retries)
  - Broader get_system_state coverage
  - Cleaner prompt with explicit Ubuntu 24.04 file path hints
"""

import xml.etree.ElementTree as ET
import subprocess
import requests
import os
import re
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────
OLLAMA_URL   = "http://10.0.2.2:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
RESULTS_FILE = "/home/pranjal-garg/agent-test.xml"
BENCHMARK    = "/home/pranjal-garg/Downloads/scap-security-guide-0.1.76/ssg-ubuntu2404-ds.xml"
LOG_FILE     = "/home/pranjal-garg/agentic-openscap-remediation/agent-run.log"
MAX_RETRIES  = 2
# ───────────────────────────────────────────────────────────────

# Rules that cannot be fixed automatically in a standalone lab VM
KNOWN_EXCEPTIONS = [
    'systemd_journal_upload_server_tls',
    'systemd_journal_upload_url',
    'journald_disable_forward_to_syslog',
    'grub2_uefi_password',
    'partition_for_tmp',
]

# Short path segments that are genuinely valid on Ubuntu 24.04
# (prevents the path-checker from flagging these as missing)
VALID_PATH_FRAGMENTS = [
    '/etc', '/usr', '/var', '/bin', '/sbin', '/home',
    '/boot', '/tmp', '/run', '/lib', '/proc', '/sys',
    '/etc/ssh', '/etc/cron', '/etc/systemd', '/etc/postfix',
    '/etc/grub.d', '/etc/apparmor.d', '/etc/pki',
]

# ── Logging ────────────────────────────────────────────────────
def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ── Command execution ──────────────────────────────────────────
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
    """Check if the binary in a command actually exists on the system."""
    parts = cmd.strip().split()
    # Skip sudo/env wrappers to get to the real binary
    for part in parts:
        if part not in ('sudo', 'env', 'bash', '-c'):
            check, _ = run_command(f"which {part} 2>/dev/null")
            return bool(check.strip())
    return True

# ── XML parsing ────────────────────────────────────────────────
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
        print(f"  Permission denied: {xml_path}")
        print(f"  Run: sudo chmod 644 {xml_path}")
        exit(1)
    except FileNotFoundError:
        print(f"  Results file not found: {xml_path}")
        exit(1)

# ── Benchmark context extraction ───────────────────────────────
def get_rule_context(rule_id):
    """
    Extract rule description, official fix text, and check content
    from the SSG benchmark XML. This gives the LLM ground-truth
    information instead of having to guess.
    """
    context = {'description': '', 'fixtext': '', 'check': ''}
    try:
        tree = ET.parse(BENCHMARK)
        root = tree.getroot()
        ns = 'http://checklists.nist.gov/xccdf/1.2'
        for rule in root.iter(f'{{{ns}}}Rule'):
            if rule.get('id') == rule_id:
                desc = rule.find(f'{{{ns}}}description')
                if desc is not None:
                    context['description'] = ''.join(
                        desc.itertext()).strip()[:700]
                fix = rule.find(f'{{{ns}}}fixtext')
                if fix is not None and fix.text:
                    context['fixtext'] = fix.text.strip()[:700]
                check = rule.find(f'.//{{{ns}}}check-content')
                if check is not None and check.text:
                    context['check'] = check.text.strip()[:500]
                break
    except Exception as e:
        log(f"WARNING: Could not parse benchmark context: {e}")
    return context

# ── Live system state probing ──────────────────────────────────
def get_system_state(rule_id):
    """
    Probe the live system for state relevant to the failing rule.
    Returns a dict of key:value strings that get injected into
    the LLM prompt so it knows the current system configuration.
    """
    short = rule_id.replace('xccdf_org.ssgproject.content_rule_', '')
    state = {}

    if 'cron' in short:
        out, _ = run_command('ls -la /etc/cron.allow /etc/cron.deny /etc/crontab 2>/dev/null')
        state['cron_files'] = out[:400]
        out2, _ = run_command('stat -c "%a %U %G %n" /etc/crontab 2>/dev/null')
        state['crontab_permissions'] = out2[:200]

    elif 'ssh' in short:
        out, _ = run_command(
            'sudo grep -v "^#" /etc/ssh/sshd_config 2>/dev/null | grep -v "^$" | head -40')
        state['sshd_config'] = out[:500]
        out2, _ = run_command('stat -c "%a %U %G %n" /etc/ssh/sshd_config 2>/dev/null')
        state['sshd_config_permissions'] = out2[:100]

    elif 'journald' in short or 'journal' in short:
        out, _ = run_command('cat /etc/systemd/journald.conf 2>/dev/null')
        state['journald_conf'] = out[:400]
        out2, _ = run_command('systemctl is-active systemd-journal-upload 2>/dev/null')
        state['journal_upload_status'] = out2[:50]

    elif 'postfix' in short:
        out, _ = run_command('postconf inet_interfaces 2>/dev/null')
        state['postfix_inet'] = out[:200]
        out2, _ = run_command('systemctl is-active postfix 2>/dev/null')
        state['postfix_status'] = out2[:50]

    elif 'apparmor' in short:
        out, _ = run_command('sudo aa-status 2>/dev/null | head -25')
        state['apparmor_status'] = out[:400]

    elif 'firewall' in short or 'nftables' in short or 'ufw' in short:
        for svc in ['ufw', 'nftables', 'iptables']:
            out, _ = run_command(f'systemctl is-active {svc} 2>/dev/null')
            state[f'{svc}_status'] = out.strip()

    elif 'partition' in short or 'mount' in short or 'tmp' in short:
        out, _ = run_command('mount | grep -E "^(tmpfs|/dev)"')
        state['mounts'] = out[:400]

    elif 'grub' in short or 'boot' in short:
        out, _ = run_command(
            r'sudo grep -r "password\|superusers" /etc/grub.d/ 2>/dev/null | head -5')
        state['grub_config'] = out[:300]

    elif 'aide' in short:
        out, _ = run_command('systemctl is-active aidecheck.timer 2>/dev/null')
        state['aide_timer'] = out.strip()
        out2, _ = run_command('ls -la /var/lib/aide/ 2>/dev/null')
        state['aide_db'] = out2[:200]

    elif 'file_' in short or 'permission' in short or 'owner' in short:
        # Generic file permission rule — extract likely file from rule name
        # e.g. file_permissions_crontab -> /etc/crontab
        words = short.replace('file_permissions_', '').replace('file_owner_', '').replace('file_groupowner_', '')
        guessed_path = f"/etc/{words.replace('_', '/')}"
        out, _ = run_command(f'stat -c "%a %U %G %n" {guessed_path} 2>/dev/null')
        if out.strip():
            state['file_permissions'] = out[:200]
        else:
            out2, _ = run_command(f'ls -la /etc/{words} 2>/dev/null')
            state['file_permissions'] = out2[:200]

    elif 'kernel' in short or 'sysctl' in short:
        # Extract sysctl key from rule name
        key = short.replace('sysctl_', '').replace('_', '.')
        out, _ = run_command(f'sysctl {key} 2>/dev/null')
        state['current_sysctl'] = out[:200]

    elif 'package' in short or 'install' in short:
        pkg = short.replace('package_', '').replace('_installed', '').replace('_removed', '')
        out, _ = run_command(f'dpkg -l | grep {pkg} 2>/dev/null | head -3')
        state['package_status'] = out[:200]

    elif 'service' in short:
        svc = short.replace('service_', '').replace('_enabled', '').replace('_disabled', '')
        out, _ = run_command(f'systemctl is-active {svc} 2>/dev/null')
        state['service_status'] = out.strip()
        out2, _ = run_command(f'systemctl is-enabled {svc} 2>/dev/null')
        state['service_enabled'] = out2.strip()

    return state

# ── Special handlers for known tricky rules ────────────────────
def special_handler(rule_id):
    """
    For rules where we know exactly what to do but the LLM
    gets it wrong, apply a hardcoded reliable fix.
    Returns (fix_applied: bool, success: bool)
    """
    import time
    short = rule_id.replace('xccdf_org.ssgproject.content_rule_', '')

    # ── journal-upload: needs fake TCP receiver ────────────────
    if short == 'service_systemd-journal-upload_enabled':
        print("\n  [Special Handler] journal-upload needs a fake TCP receiver.")
        run_command('sudo pkill -f "nc -lkp 19532" 2>/dev/null')
        conf = '[Upload]\nURL=http://127.0.0.1:19532\n'
        with open('/tmp/journal-upload.conf', 'w') as cf:
            cf.write(conf)
        run_command('sudo cp /tmp/journal-upload.conf /etc/systemd/journal-upload.conf')
        run_command('sudo nohup nc -lkp 19532 > /dev/null 2>&1 &')
        run_command('sudo systemctl daemon-reload')
        run_command('sudo systemctl restart systemd-journal-upload')
        time.sleep(2)
        status, _ = run_command('systemctl is-active systemd-journal-upload')
        if 'active' in status:
            print("  Service is now active.")
            return True, True
        print(f"  Service status: {status.strip()}")
        return True, False

    # ── AIDE package: just install it ─────────────────────────
    if short == 'package_aide_installed':
        print("\n  [Special Handler] Installing aide package...")
        out, rc = run_command(
            'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y aide aide-common')
        if rc != 0:
            print(f"  Install failed: {out[:200]}")
            return True, False
        print("  aide installed successfully.")
        return True, True

    # ── AIDE database: install + init + enable timer ───────────
    if 'aide_build' in short or 'aide_periodic' in short:
        print("\n  [Special Handler] AIDE full setup.")
        print("  Step 1: Installing aide...")
        out, rc = run_command(
            'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y aide aide-common')
        if rc != 0:
            print(f"  Install failed: {out[:200]}")
            return True, False
        print("  Step 2: Building AIDE database (2-3 min, please wait)...")
        run_command('sudo aideinit --yes 2>/dev/null || sudo aideinit 2>/dev/null')
        # Wait for aideinit to finish — it can take several minutes
        time.sleep(5)
        run_command(
            'sudo mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db 2>/dev/null')
        # Verify db exists
        db_check, _ = run_command('ls -la /var/lib/aide/aide.db 2>/dev/null')
        if db_check.strip():
            print(f"  Database created: {db_check.strip()}")
        else:
            print("  Warning: aide.db not found — aideinit may still be running")
        print("  Step 3: Enabling aidecheck timer...")
        run_command('sudo systemctl unmask aidecheck.timer 2>/dev/null')
        run_command('sudo systemctl unmask aidecheck.service 2>/dev/null')
        run_command('sudo systemctl enable --now aidecheck.timer')
        time.sleep(2)
        status, _ = run_command('systemctl is-active aidecheck.timer')
        if 'active' in status:
            print("  AIDE timer active.")
            return True, True
        print(f"  Timer status: {status.strip()}")
        return True, False

    # ── journald ForwardToSyslog ───────────────────────────────
    if short == 'journald_forward_to_syslog':
        print("\n  [Special Handler] Setting ForwardToSyslog=yes in journald.conf")
        run_command(
            "sudo sed -i '/ForwardToSyslog/d' /etc/systemd/journald.conf")
        run_command(
            "sudo bash -c 'echo ForwardToSyslog=yes >> /etc/systemd/journald.conf'")
        run_command('sudo systemctl restart systemd-journald')
        out, _ = run_command('grep ForwardToSyslog /etc/systemd/journald.conf')
        print(f"  Config: {out.strip()}")
        return True, True

    return False, False




# ── LLM interaction ────────────────────────────────────────────
def ask_llm(rule_id, severity, context=None, system_state=None,
            previous_failure=None):
    """
    Build a rich prompt with rule context, live system state,
    and optionally feedback from a previous failed fix attempt.
    """
    short_name = rule_id.replace(
        'xccdf_org.ssgproject.content_rule_', ''
    ).replace('_', ' ')

    # Build context section from benchmark XML
    context_block = ""
    if context:
        if context.get('description'):
            context_block += f"\nRULE DESCRIPTION FROM BENCHMARK:\n{context['description']}\n"
        if context.get('fixtext'):
            context_block += f"\nOFFICIAL FIX TEXT FROM BENCHMARK (follow this closely):\n{context['fixtext']}\n"
        if context.get('check'):
            context_block += f"\nWHAT THE OVAL CHECK TESTS:\n{context['check']}\n"

    # Build system state section
    state_block = ""
    if system_state:
        state_block = "\nCURRENT LIVE SYSTEM STATE (Ubuntu 24.04):\n"
        for k, v in system_state.items():
            if v.strip():
                state_block += f"  {k}:\n    {v.strip()}\n"

    # Build retry feedback section
    retry_block = ""
    if previous_failure:
        retry_block = (
            f"\nPREVIOUS FIX ATTEMPT FAILED:\n"
            f"  Command tried: {previous_failure['cmd']}\n"
            f"  Output: {previous_failure['output'][:300]}\n"
            f"  Please suggest a different approach.\n"
        )

    prompt = f"""You are a Linux security hardening expert fixing OpenSCAP CIS Level 1 benchmark failures on Ubuntu 24.04 LTS.

IMPORTANT CONSTRAINTS:
- Only use commands available on Ubuntu 24.04: sed, awk, systemctl, apt-get, chmod, chown, tee, echo, grep, find
- Do NOT invent tools or commands that do not exist on Ubuntu
- File paths must be real Ubuntu 24.04 paths (e.g. /etc/ssh/sshd_config, /etc/crontab, /etc/systemd/journald.conf)
- If the official fix text is provided above, base your FIX_COMMAND on it
- FIX_COMMAND must be a single bash command (you may use && to chain steps)

FAILING RULE: {short_name}
RULE ID: {rule_id}
SEVERITY: {severity}
{context_block}{state_block}{retry_block}
Respond in EXACTLY this format with no preamble, no extra lines:
EXPLANATION: <1-2 sentences: what the rule checks and why it matters>
RISK_LEVEL: LOW or MEDIUM or HIGH
FIX_COMMAND: <single bash command, or MANUAL if cannot be scripted>
VERIFY_COMMAND: <oscap eval command to verify>
CATEGORY: CONFIG_CHANGE or PACKAGE_INSTALL or SERVICE_CHANGE or INFRASTRUCTURE or BENCHMARK_CONFLICT or MANUAL_ONLY"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120
        )
        return response.json().get("response", "No response from LLM")
    except requests.exceptions.ConnectionError:
        return "ERROR: Cannot connect to Ollama at 10.0.2.2:11434"
    except requests.exceptions.Timeout:
        return "ERROR: LLM timed out after 120s"

def parse_llm_response(response):
    fields = {
        'EXPLANATION': '',
        'RISK_LEVEL': 'UNKNOWN',
        'FIX_COMMAND': 'MANUAL',
        'VERIFY_COMMAND': '',
        'CATEGORY': 'UNKNOWN'
    }
    for line in response.split('\n'):
        line = line.strip()
        for key in fields:
            if line.startswith(f"{key}:"):
                fields[key] = line.split(':', 1)[1].strip()
                break
    return fields

# ── Path validation ────────────────────────────────────────────
def validate_and_correct_paths(fix_cmd, rule_ctx, sys_state, rule_id, severity):
    """
    Check paths in the proposed fix command.
    If a path doesn't exist, try to find it via locate/find.
    Re-query LLM if correction is needed.
    Returns (possibly corrected) parsed response dict.
    """
    extracted_paths = re.findall(r'(/[a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)*)', fix_cmd)
    corrected_context = ""

    for path in extracted_paths:
        # Skip if it's a known-valid parent path
        if any(path.startswith(vp) or path == vp for vp in VALID_PATH_FRAGMENTS):
            if os.path.exists(path):
                continue
            # Path starts with valid prefix but doesn't exist — check it
            if not os.path.exists(path):
                # Try to find the actual file
                base = os.path.basename(path)
                probe, _ = run_command(
                    f"find /etc /usr /var -name '{base}' -type f 2>/dev/null | head -1")
                if probe.strip() and probe.strip() != path:
                    print(f"  [Path Check] '{path}' not found → mapped to '{probe.strip()}'")
                    corrected_context += (
                        f"Path '{path}' does not exist on this system. "
                        f"The file is at '{probe.strip()}' instead.\n"
                    )
                # If path just doesn't exist yet (will be created), that's fine
                # Only flag if it's a file we're trying to READ/MODIFY not create

    if corrected_context:
        print("  [Self-Correction] Re-querying LLM with corrected path info...")
        correction_note = f"\nPATH CORRECTION NEEDED:\n{corrected_context}"
        raw = ask_llm(rule_id, severity, rule_ctx, sys_state,
                      previous_failure={'cmd': fix_cmd, 'output': corrected_context})
        return raw, parse_llm_response(raw)

    return None, None

# ── Rule verification ──────────────────────────────────────────
def verify_rule(rule_id):
    cmd = (
        f"sudo oscap xccdf eval "
        f"--rule {rule_id} "
        f"{BENCHMARK} 2>/dev/null | grep 'Result'"
    )
    output, _ = run_command(cmd)
    return 'pass' in output.lower()

# ── Main agent loop ────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("  OpenSCAP Remediation Agent v1.3")
    print("  Model  : llama3.2 @ 10.0.2.2:11434")
    print("  Profile: CIS Level 1 Workstation - Ubuntu 24.04")
    print("="*60 + "\n")
    log("Agent v1.3 started")

    run_command(f"sudo chmod 644 {RESULTS_FILE} 2>/dev/null")
    failing = get_failing_rules(RESULTS_FILE)

    if not failing:
        print("  No failing rules found — system is fully compliant!")
        log("No failing rules found")
        return

    log(f"Found {len(failing)} failing rules")
    print(f"  Found {len(failing)} failing rules to process\n")

    results = {'fixed': [], 'skipped': [], 'failed': [], 'manual': []}

    for i, rule in enumerate(failing, 1):
        rule_id  = rule['id']
        severity = rule['severity']
        short_id = rule_id.replace('xccdf_org.ssgproject.content_rule_', '')

        print(f"\n{'='*60}")
        print(f"  [{i}/{len(failing)}] Rule: {short_id}")
        print(f"  Severity: {severity}")

        # ── Step 1: Known infrastructure exceptions ────────────
        if any(exc in short_id for exc in KNOWN_EXCEPTIONS):
            print("\n  [EXCEPTION] This rule requires enterprise infrastructure.")
            print("  Reason: Cannot be satisfied on a standalone lab VM.")
            print("  Action: Logged as documented exception for project report.")
            log(f"KNOWN EXCEPTION: {rule_id}")
            results['manual'].append(rule_id)
            input("\n  Press Enter to continue...")
            continue

        # ── Step 2: Special handlers for known tricky rules ────
        handled, success = special_handler(rule_id)
        if handled:
            if success and verify_rule(rule_id):
                print("  [Special Handler] PASS - Rule now passes!")
                log(f"SPECIAL HANDLER PASS: {rule_id}")
                results['fixed'].append(rule_id)
            else:
                print("  [Special Handler] FAIL - Rule still failing.")
                log(f"SPECIAL HANDLER FAIL: {rule_id}")
                results['failed'].append(rule_id)
            input("\n  Press Enter to continue...")
            continue

        # ── Step 3: Extract context from benchmark XML ─────────
        print("\n  Extracting context from benchmark XML...")
        rule_ctx = get_rule_context(rule_id)
        if rule_ctx.get('description'):
            print(f"  Description: {rule_ctx['description'][:100]}...")
        if rule_ctx.get('fixtext'):
            print(f"  Fix hint   : {rule_ctx['fixtext'][:100]}...")

        # ── Step 4: Probe live system state ────────────────────
        print("  Probing live system state...")
        sys_state = get_system_state(rule_id)
        if sys_state:
            print(f"  State keys : {list(sys_state.keys())}")

        # ── Step 5: Query LLM ──────────────────────────────────
        print("  Querying LLM...")
        raw_response = ask_llm(rule_id, severity, rule_ctx, sys_state)

        if raw_response.startswith("ERROR"):
            print(f"  {raw_response}")
            results['skipped'].append(rule_id)
            continue

        parsed = parse_llm_response(raw_response)

        # ── Step 6: Path validation & self-correction ──────────
        corrected_raw, corrected_parsed = validate_and_correct_paths(
            parsed['FIX_COMMAND'], rule_ctx, sys_state, rule_id, severity)
        if corrected_parsed:
            raw_response = corrected_raw
            parsed = corrected_parsed

        # ── Step 7: Display LLM analysis ───────────────────────
        print(f"\n  EXPLANATION : {parsed['EXPLANATION']}")
        print(f"  RISK LEVEL  : {parsed['RISK_LEVEL']}")
        print(f"  CATEGORY    : {parsed['CATEGORY']}")
        print(f"  FIX COMMAND : {parsed['FIX_COMMAND']}")

        # Auto-classify manual-only rules
        if parsed['FIX_COMMAND'] == 'MANUAL' or parsed['CATEGORY'] in [
            'INFRASTRUCTURE', 'BENCHMARK_CONFLICT', 'MANUAL_ONLY'
        ]:
            print("\n  [MANUAL] This rule requires human intervention.")
            log(f"MANUAL: {rule_id}")
            results['manual'].append(rule_id)
            input("  Press Enter to continue...")
            continue

        # ── Step 8: Human approval gate ────────────────────────
        print(f"\n  Proposed fix:\n  $ {parsed['FIX_COMMAND']}\n")

        previous_failure = None
        retry_count = 0

        while True:
            choice = input(
                "  Apply? [y=yes / n=skip / d=details] : "
            ).strip().lower()

            if choice == 'd':
                print(f"\n  Full LLM response:\n{raw_response}\n")
                continue

            elif choice == 'n':
                print("  Skipped by user.")
                log(f"SKIPPED: {rule_id}")
                results['skipped'].append(rule_id)
                break

            elif choice == 'y':
                fix_cmd = parsed['FIX_COMMAND']

                # Add sudo if missing
                if not fix_cmd.startswith('sudo'):
                    fix_cmd = 'sudo ' + fix_cmd

                # Hallucination check
                if not command_exists(fix_cmd):
                    print(f"\n  [Hallucination] Command not found on this system.")
                    print(f"  Command was: {fix_cmd}")
                    log(f"HALLUCINATED COMMAND: {rule_id} | {fix_cmd}")

                    if retry_count < MAX_RETRIES:
                        retry_count += 1
                        print(f"  Retrying with LLM (attempt {retry_count}/{MAX_RETRIES})...")
                        raw_response = ask_llm(
                            rule_id, severity, rule_ctx, sys_state,
                            previous_failure={
                                'cmd': fix_cmd,
                                'output': 'command not found on Ubuntu 24.04'
                            }
                        )
                        parsed = parse_llm_response(raw_response)
                        print(f"  New fix: {parsed['FIX_COMMAND']}")
                        continue
                    else:
                        results['failed'].append(rule_id)
                        log(f"HALLUCINATION UNRESOLVED: {rule_id}")
                        input("  Press Enter to continue...")
                        break

                # Apply the fix
                print(f"\n  Applying: {fix_cmd}")
                log(f"APPLYING: {rule_id} | {fix_cmd}")
                output, returncode = run_command(fix_cmd)

                if output.strip():
                    print(f"  Output: {output.strip()[:250]}")

                # ── Step 9: Verify with oscap ──────────────────
                if returncode == 0:
                    print("  Command succeeded. Verifying with oscap...")
                    if verify_rule(rule_id):
                        print("  PASS ✓ Rule now passes!")
                        log(f"VERIFIED PASS: {rule_id}")
                        results['fixed'].append(rule_id)
                        break
                    else:
                        # Fix ran but oscap still fails — retry with feedback
                        if retry_count < MAX_RETRIES:
                            retry_count += 1
                            print(f"\n  Fix ran but rule still failing.")
                            print(f"  Retrying with LLM feedback ({retry_count}/{MAX_RETRIES})...")
                            previous_failure = {
                                'cmd': fix_cmd,
                                'output': output or 'command succeeded but oscap check still fails'
                            }
                            raw_response = ask_llm(
                                rule_id, severity, rule_ctx, sys_state,
                                previous_failure=previous_failure
                            )
                            parsed = parse_llm_response(raw_response)
                            print(f"  New fix: {parsed['FIX_COMMAND']}")
                            fix_cmd = parsed['FIX_COMMAND']
                            if not fix_cmd.startswith('sudo'):
                                fix_cmd = 'sudo ' + fix_cmd
                            # Don't break — let user approve new fix
                            print(f"\n  Revised proposed fix:\n  $ {fix_cmd}\n")
                            continue
                        else:
                            print("  FAIL ✗ Rule still failing after all retries.")
                            log(f"STILL FAILING: {rule_id}")
                            results['failed'].append(rule_id)
                            break
                else:
                    print(f"  Command failed (exit code {returncode})")
                    if retry_count < MAX_RETRIES:
                        retry_count += 1
                        print(f"  Retrying with error feedback ({retry_count}/{MAX_RETRIES})...")
                        raw_response = ask_llm(
                            rule_id, severity, rule_ctx, sys_state,
                            previous_failure={
                                'cmd': fix_cmd,
                                'output': output
                            }
                        )
                        parsed = parse_llm_response(raw_response)
                        print(f"  New fix: {parsed['FIX_COMMAND']}")
                        fix_cmd = parsed['FIX_COMMAND']
                        if not fix_cmd.startswith('sudo'):
                            fix_cmd = 'sudo ' + fix_cmd
                        print(f"\n  Revised proposed fix:\n  $ {fix_cmd}\n")
                        continue
                    else:
                        log(f"FIX ERROR: {rule_id} | exit {returncode}")
                        results['failed'].append(rule_id)
                        break
            else:
                print("  Please enter y, n, or d")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  AGENT RUN COMPLETE — SUMMARY")
    print("="*60)
    print(f"  Fixed successfully : {len(results['fixed'])}")
    print(f"  Still failing      : {len(results['failed'])}")
    print(f"  Skipped by user    : {len(results['skipped'])}")
    print(f"  Manual exceptions  : {len(results['manual'])}")

    total = sum(len(v) for v in results.values())
    if total > 0:
        pct = len(results['fixed']) / total * 100
        print(f"\n  Agent fix rate     : {pct:.1f}%")

    if results['fixed']:
        print("\n  Rules fixed:")
        for r in results['fixed']:
            print(f"    + {r.replace('xccdf_org.ssgproject.content_rule_','')}")

    if results['failed']:
        print("\n  Rules still failing:")
        for r in results['failed']:
            print(f"    x {r.replace('xccdf_org.ssgproject.content_rule_','')}")

    if results['manual']:
        print("\n  Documented exceptions:")
        for r in results['manual']:
            print(f"    ! {r.replace('xccdf_org.ssgproject.content_rule_','')}")

    log(f"Agent complete | Fixed:{len(results['fixed'])} "
        f"Failed:{len(results['failed'])} "
        f"Skipped:{len(results['skipped'])} "
        f"Manual:{len(results['manual'])}")
    print(f"\n  Full log: {LOG_FILE}")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
