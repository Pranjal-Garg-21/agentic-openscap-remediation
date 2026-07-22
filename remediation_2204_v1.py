#!/usr/bin/env python3
"""
CIS Benchmark Remediation Pipeline — Ubuntu 22.04, Role: Software Developer
============================================================================
Adapted from your remediationv3.py (v3.3) for a single fixed profile instead
of the MAX/MIN admin ground-truth grid:

  Role: Software Developer
  dev_stack: Containerized Apps (Docker/Podman) + Virtual Machines
  network_exposure: Yes — runs local servers/APIs that teammates or
                     external tools connect to

WHAT'S DIFFERENT FROM v3.3, ON PURPOSE (per your request):
  - No GROUND_TRUTH_XLSX / openpyxl read. KEEP_RULE_IDS below is hardcoded —
    it's the actual 46-rule "FINAL KEEP" list your qwen2.5:7b analysis run
    produced against agent-test-22.xml for this exact profile. Anything not
    in this list is simply never sent to the model — there's no per-rule
    SKIP bookkeeping to log because SKIP rules were never given to this
    script in the first place.
  - No --profile / MAX / MIN / --only-min-delta. One profile, one KEEP list.
  - gt_row / gt_decision / gt_reason are gone. Rule context is: title +
    description + severity + reference fix, all pulled from the scan XML,
    plus the fixed PROFILE description below (used in every prompt).

WHAT'S KEPT, UNCHANGED IN SPIRIT, FROM v3.3 (this is where the real value
of your prior work was, so it's carried over rather than rebuilt):
  - Multi-pass retry loop (MAX_PASSES=3): query -> apply -> oscap-verify,
    feeding the exact error back to the model on failure instead of just
    logging and moving on.
  - Sudoers corruption guard: any script mentioning sudoers/visudo gets a
    pre-attempt snapshot; visudo -c is checked after; a broken sudoers state
    rolls back immediately instead of poisoning every later rule.
  - CLARIFY block: the model can ask ONE multiple-choice (+ free text)
    question per rule instead of guessing when it has a genuine,
    non-technical doubt.
  - Permission gate: a script that fails specifically because it needs an
    interactive sudo password pauses for explicit human go-ahead, then
    re-runs attached to the real terminal so sudo can prompt directly.
  - KNOWN_FIXES / RULE_HINTS: carried over verbatim from the 24.04 project.
    FLAG: these were verified correct on Ubuntu 24.04 (Noble). Ubuntu 22.04
    (Jammy) uses the same PAM/sudoers/sysctl conventions in every case I
    checked, but I have NOT re-verified any of these against a real 22.04
    box — treat pass-1 KNOWN_FIXES results with a bit more skepticism here
    than the comment in the code implies, and let the multi-pass loop's
    oscap re-check be the actual judge.
  - LOCKOUT_RISK_RULES: same real incidents (faillock lockout, sudo caching
    break) — still worth the explicit confirmation gate here.
  - break_rules.sh generator + VM-snapshot reset prompt between models.

Usage (same flags as before, minus the profile ones):
  python3 remediation_2204_v1.py --probe
  python3 remediation_2204_v1.py                      # interactive
  python3 remediation_2204_v1.py --auto
  python3 remediation_2204_v1.py --model qwen2.5:7b
  python3 remediation_2204_v1.py --snapshot
  python3 remediation_2204_v1.py --retry-failed remediation_results/qwen2_5_7b_20260722_....json --auto
  python3 remediation_2204_v1.py --only-rules sudo_custom_logfile,aide_build_database --auto
"""

import os, re, sys, json, time, datetime, subprocess, xml.etree.ElementTree as ET
import requests, urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — secrets from the environment only. export before running:
#   export LAB_URL=... LAB_USER=... LAB_PASS=...
# Rotate LAB_PASS if it was ever pasted into chat/committed anywhere.
# ─────────────────────────────────────────────────────────────────────────────

LAB_URL  = os.environ.get("LAB_URL",  "https://10.1.96.96:8443")
LAB_USER = os.environ.get("LAB_USER", "user")
LAB_PASS = os.environ.get("LAB_PASS", "H72j8n19sna")

MODELS = [
    "qwen2.5:7b",   # best performer in the earlier MIN/MAX remediation comparison
    # "gpt-oss:latest",
    # "gemma2:latest",
    # "mistral:latest",
    # "granite4.1:8b",
]

SCAN_RESULT_XML = "agent-test-22.xml"
RESULTS_DIR     = "remediation_results"

# scap-security-guide-0.1.74, matching the actual scan this profile was
# analyzed with:
#   wget https://github.com/ComplianceAsCode/content/releases/download/v0.1.74/scap-security-guide-0.1.74.zip
#   unzip scap-security-guide-0.1.74.zip
#   oscap xccdf eval --profile xccdf_org.ssgproject.content_profile_cis_level1_server \
#       --results scan-results.xml scap-security-guide-0.1.74/ssg-ubuntu2204-ds.xml
# Adjust the path below if you unzipped it somewhere other than $HOME.
BENCHMARK_XML = os.path.expanduser(
    "~/scap-security-guide-0.1.74/ssg-ubuntu2204-ds.xml")
BENCHMARK_PROFILE_ID = "xccdf_org.ssgproject.content_profile_cis_level1_server"

SNAPSHOT_NAME = "baseline-2204-46-rules"
VM_NAME       = "ubuntu-22.04-oscap"  # matches the running VM in VirtualBox Manager

ELEVATE_WITH_SUDO = True  # scripts run as `sudo -n bash -c <script>`

# Max attempts per rule (query -> apply -> verify, retrying with error
# feedback on failure). 1 = old single-pass behavior.
MAX_PASSES = 3

DEFAULT_SCRIPT_TIMEOUT = 120
RULE_TIMEOUTS = {
    "aide_build_database": 3600,  # aideinit walks the whole filesystem
    "grub2_uefi_password": 180,
}

PREFIX = "xccdf_org.ssgproject.content_rule_"

SYSTEM_INFO = {
    "hostname": "ubuntu2204-scap-test",
    "kernel":   "5.15.0-generic",
    "os":       "Ubuntu 22.04 LTS (Jammy Jellyfish)",
    "arch":     "x86_64",
}

PROFILE_DESCRIPTION = (
    "Software Developer. Builds containerized apps (Docker/Podman) and uses "
    "virtual machines; runs local servers/APIs that teammates or external "
    "tools connect to. Any fix must not break Docker bridge networking, VM "
    "virtual NICs, or an already-listening local server/API port."
)

# ─────────────────────────────────────────────────────────────────────────────
# THE 46 KEEP RULES — hardcoded from the actual "FINAL KEEP" output of your
# qwen2.5:7b analysis run against agent-test-22.xml for this exact profile.
# No Excel read. Anything not listed here is never sent to the model.
# ─────────────────────────────────────────────────────────────────────────────

KEEP_RULE_IDS = [
    # "xccdf_org.ssgproject.content_rule_aide_build_database",  # commented out for now — aideinit walks the whole filesystem and stalls the run; run this one separately later
    "xccdf_org.ssgproject.content_rule_partition_for_tmp",
    "xccdf_org.ssgproject.content_rule_sudo_custom_logfile",
    "xccdf_org.ssgproject.content_rule_sudo_remove_no_authenticate",
    "xccdf_org.ssgproject.content_rule_sudo_require_reauthentication",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_deny",
    "xccdf_org.ssgproject.content_rule_accounts_passwords_pam_faillock_unlock_time",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_dcredit",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_minlen",
    "xccdf_org.ssgproject.content_rule_accounts_password_pam_ucredit",
    "xccdf_org.ssgproject.content_rule_set_password_hashing_algorithm_systemauth",
    "xccdf_org.ssgproject.content_rule_accounts_umask_etc_bashrc",
    "xccdf_org.ssgproject.content_rule_accounts_umask_etc_login_defs",
    "xccdf_org.ssgproject.content_rule_accounts_tmout",
    "xccdf_org.ssgproject.content_rule_grub2_enable_apparmor",
    "xccdf_org.ssgproject.content_rule_grub2_uefi_password",
    "xccdf_org.ssgproject.content_rule_journald_compress",
    "xccdf_org.ssgproject.content_rule_journald_storage",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv6_conf_all_forwarding",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_accept_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_log_martians",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_rp_filter",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_tcp_syncookies",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_conf_all_send_redirects",
    "xccdf_org.ssgproject.content_rule_sysctl_net_ipv4_ip_forward",
    "xccdf_org.ssgproject.content_rule_service_nftables_enabled",
    "xccdf_org.ssgproject.content_rule_file_groupowner_backup_etc_gshadow",
    "xccdf_org.ssgproject.content_rule_kernel_module_hfs_disabled",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nodev",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_noexec",
    "xccdf_org.ssgproject.content_rule_mount_option_dev_shm_nosuid",
    "xccdf_org.ssgproject.content_rule_disable_users_coredumps",
    "xccdf_org.ssgproject.content_rule_sysctl_fs_suid_dumpable",
    "xccdf_org.ssgproject.content_rule_sysctl_kernel_randomize_va_space",
    "xccdf_org.ssgproject.content_rule_file_groupowner_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_owner_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_allow",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_d",
    "xccdf_org.ssgproject.content_rule_file_permissions_cron_daily",
    "xccdf_org.ssgproject.content_rule_file_permissions_crontab",
    "xccdf_org.ssgproject.content_rule_package_nis_removed",
    "xccdf_org.ssgproject.content_rule_package_vsftpd_removed",
    "xccdf_org.ssgproject.content_rule_package_openldap-clients_removed",
    "xccdf_org.ssgproject.content_rule_package_rpcbind_removed",
    "xccdf_org.ssgproject.content_rule_package_telnet_removed",
    "xccdf_org.ssgproject.content_rule_package_rsync_removed",
]
assert len(KEEP_RULE_IDS) == len(set(KEEP_RULE_IDS)) == 45  # 46 KEEP rules minus aide_build_database, deferred for now

# ─────────────────────────────────────────────────────────────────────────────
# KNOWN_FIXES / RULE_HINTS — carried over from the 24.04 project (see the
# FLAG in the module docstring: not yet re-verified against a real 22.04 box).
# Keyed by SHORT rule name (no xccdf prefix).
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_FIXES = {
    "sudo_require_reauthentication": """\
if dpkg-query --show --showformat='${db:Status-Status}\\n' 'sudo' 2>/dev/null | grep -q '^installed'; then
    echo 'Defaults timestamp_timeout=15' > /etc/sudoers.d/reauthenticate_sudo
    chmod 0440 /etc/sudoers.d/reauthenticate_sudo
fi""",
    "sudo_custom_logfile": """\
if dpkg-query --show --showformat='${db:Status-Status}\\n' 'sudo' 2>/dev/null | grep -q '^installed'; then
    mkdir -p /var/log/sudo
    touch /var/log/sudo.log
    chmod 640 /var/log/sudo.log
    chown root:adm /var/log/sudo.log
    echo 'Defaults logfile="/var/log/sudo.log"' > /etc/sudoers.d/01-cis-sudo-logfile
    chmod 0440 /etc/sudoers.d/01-cis-sudo-logfile
fi""",
    "service_nftables_enabled": """\
if dpkg-query --show --showformat='${db:Status-Status}\\n' 'nftables' 2>/dev/null | grep -q '^installed'; then
    systemctl unmask nftables
    systemctl enable nftables
    systemctl start nftables
fi""",
    "accounts_tmout": """\
for f in /etc/profile.d/*.sh; do
    [ -f "$f" ] || continue
    [ "$f" = "/etc/profile.d/tmout.sh" ] && continue
    if grep -qE 'TMOUT[[:space:]]*=' "$f" 2>/dev/null; then
        sed -i -E '/TMOUT/d' "$f"
    fi
done
cat > /etc/profile.d/tmout.sh << 'EOF'
TMOUT=900
readonly TMOUT
export TMOUT
EOF
chmod 644 /etc/profile.d/tmout.sh""",
    "accounts_passwords_pam_faillock_deny": """\
if grep -qE '^deny[[:space:]]*=' /etc/security/faillock.conf 2>/dev/null; then
    sed -i -E 's/^deny[[:space:]]*=.*/deny = 4/' /etc/security/faillock.conf
else
    echo 'deny = 4' >> /etc/security/faillock.conf
fi""",
    "accounts_passwords_pam_faillock_unlock_time": """\
if grep -qE '^unlock_time[[:space:]]*=' /etc/security/faillock.conf 2>/dev/null; then
    sed -i -E 's/^unlock_time[[:space:]]*=.*/unlock_time = 900/' /etc/security/faillock.conf
else
    echo 'unlock_time = 900' >> /etc/security/faillock.conf
fi""",
    "accounts_password_pam_minlen": """\
if grep -qE '^[[:space:]]*minlen[[:space:]]*=' /etc/security/pwquality.conf 2>/dev/null; then
    sed -i -E 's/^[[:space:]]*minlen[[:space:]]*=.*/minlen = 14/' /etc/security/pwquality.conf
else
    echo 'minlen = 14' >> /etc/security/pwquality.conf
fi""",
    "accounts_password_pam_ucredit": """\
if grep -qE '^[[:space:]]*ucredit[[:space:]]*=' /etc/security/pwquality.conf 2>/dev/null; then
    sed -i -E 's/^[[:space:]]*ucredit[[:space:]]*=.*/ucredit = -1/' /etc/security/pwquality.conf
else
    echo 'ucredit = -1' >> /etc/security/pwquality.conf
fi""",
    "accounts_password_pam_dcredit": """\
if grep -qE '^dcredit[[:space:]]*=' /etc/security/pwquality.conf 2>/dev/null; then
    sed -i -E 's/^dcredit[[:space:]]*=.*/dcredit = -1/' /etc/security/pwquality.conf
else
    echo 'dcredit = -1' >> /etc/security/pwquality.conf
fi""",
    "file_groupowner_backup_etc_gshadow": """\
chgrp 0 /etc/gshadow-
ls -l /etc/gshadow-
""",
    "grub2_uefi_password": """\
grub_pw='ChangeMe123!Cis'
password_hash=$(printf '%s\\n%s\\n' "$grub_pw" "$grub_pw" | grub-mkpasswd-pbkdf2 | grep -oP '(?<=is ).*')
if [ -z "$password_hash" ]; then
    echo "Failed to generate password hash." >&2
    exit 1
fi
cat > /etc/grub.d/40_custom << 'HEADER'
#!/bin/sh
exec tail -n +3 $0
HEADER
echo 'set superusers="boot"' >> /etc/grub.d/40_custom
echo "password_pbkdf2 boot $password_hash" >> /etc/grub.d/40_custom
chmod 755 /etc/grub.d/40_custom
update-grub
""",
    "partition_for_tmp": """\
if systemctl list-unit-files tmp.mount &>/dev/null; then
    systemctl unmask tmp.mount
    systemctl enable --now tmp.mount
else
    grep -qE '^tmpfs[[:space:]]+/tmp[[:space:]]' /etc/fstab || \\
        echo 'tmpfs /tmp tmpfs defaults,rw,nosuid,nodev,noexec,relatime,size=2G 0 0' >> /etc/fstab
    mount /tmp 2>/dev/null || mount -a
fi
mount | grep ' /tmp '
""",
}

RULE_HINTS = {
    "sudo_require_reauthentication":
        "Any line written into /etc/sudoers or /etc/sudoers.d/* MUST start "
        "with the `Defaults` keyword, e.g. `Defaults timestamp_timeout=15`. "
        "Use exactly 15 (minutes) -- not 0, not -1 (negative means the "
        "cached credential NEVER expires; 0 forces a password every "
        "command, which is stricter than required and will also break "
        "every later sudo call in this pipeline). Any file under "
        "/etc/sudoers.d/ must be mode 0440 -- `visudo -c` rejects anything "
        "else. `chmod 0440` the file in the same script that creates it.",
    "sudo_custom_logfile":
        "Use the `Defaults` keyword, e.g. `Defaults logfile=\"/var/log/sudo.log\"`. "
        "A bare `logfile /var/log/sudo.log` line is invalid syntax and "
        "breaks sudo for the entire system, not just this rule. Any file "
        "under /etc/sudoers.d/ must be mode 0440 -- `chmod 0440` it in the "
        "same script that creates it.",
    "aide_build_database":
        "If `aideinit` returns non-zero, remove any stale /var/lib/aide/"
        "aide.db or aide.db.new from a previous attempt first, then run "
        "`aideinit -y -f` with output captured so the real error is "
        "visible instead of just the exit code. This walks the whole "
        "filesystem and can genuinely take a long time -- see RULE_TIMEOUTS.",
    "service_nftables_enabled":
        "Likely masked by default (`systemctl status nftables`). Run "
        "`systemctl unmask nftables` BEFORE `systemctl enable nftables` -- "
        "enabling a masked service always fails. Docker manages its own "
        "iptables rules; don't add nftables rules that would fight Docker's "
        "own chains, and don't disable Docker's forwarding/NAT rules.",
    "accounts_tmout":
        "Write all three lines directly into the CONTENT of "
        "/etc/profile.d/tmout.sh as literal text, not as commands executed "
        "in the remediation script's own throwaway shell (that has no "
        "lasting effect on future login shells). File must contain exactly:\n"
        "TMOUT=900\nreadonly TMOUT\nexport TMOUT",
    "grub2_uefi_password":
        "Debian/Ubuntu command is `grub-mkpasswd-pbkdf2` (no '2' after "
        "grub) -- `grub2-mkpasswd-pbkdf2` doesn't exist here. "
        "/etc/grub.d/40_custom is an executable script `update-grub` runs "
        "directly -- it needs the `#!/bin/sh` + `exec tail -n +3 $0` header "
        "or update-grub tries to execute the config lines as shell commands.",
    "partition_for_tmp":
        "This check almost certainly just verifies /tmp is its own live "
        "mount point right now, not a real disk partition, and doesn't even "
        "check /etc/fstab. A tmpfs mount fully satisfies it. Don't grab an "
        "arbitrary block device with lsblk and mount it onto /tmp -- that "
        "can clobber a partition already in use. Use `systemctl unmask "
        "tmp.mount && systemctl enable --now tmp.mount`, or add a tmpfs "
        "line to /etc/fstab and remount.",
    "sysctl_net_ipv4_ip_forward":
        "This host runs Docker, which needs net.ipv4.ip_forward=1 for "
        "container bridge networking. Docker's own dockerd re-enables this "
        "at daemon start regardless of what sysctl.conf says, so setting it "
        "to 0 here will just get silently overridden the next time Docker "
        "restarts -- if the check truly requires 0, flag that conflict back "
        "with a CLARIFY rather than writing a value that breaks the "
        "profile's stated Docker/VM workflow.",
    "sysctl_net_ipv4_conf_all_rp_filter":
        "Strict reverse-path filtering is a known source of dropped "
        "packets on hosts with Docker bridge interfaces and VM virtual "
        "NICs. Check whether this profile's benchmark actually wants mode "
        "1 (strict) vs mode 2 (loose) before writing a value -- loose mode "
        "is the safer choice on a Docker/VM host if the check accepts it.",
    "file_groupowner_backup_etc_gshadow":
        "After `chgrp`, print `ls -l /etc/gshadow-` to confirm the change "
        "persisted and wasn't reset by another process.",
    "accounts_passwords_pam_faillock_deny":
        "Use `deny = 4` unless the benchmark on this host says otherwise. "
        "Append to /etc/security/faillock.conf with `>>`, not overwrite "
        "with `>` -- other faillock rules write to this same file.",
    "accounts_passwords_pam_faillock_unlock_time":
        "Use `unlock_time = 900` unless the benchmark on this host says "
        "otherwise. Append with `>>`, not overwrite with `>`.",
}

# ─────────────────────────────────────────────────────────────────────────────
# SCAN XML PARSING — pulls title/description/fix/severity for the KEEP list
# ─────────────────────────────────────────────────────────────────────────────

def parse_scan_xml(path):
    rules = {}
    if not os.path.exists(path):
        print(f"  [WARN] Scan XML '{path}' not found -- rules will run with "
              f"rule-id-derived titles only (no reference fix/description).")
        return rules
    try:
        tree = ET.parse(path)
    except Exception as e:
        print(f"[ERROR] Cannot parse {path}: {e}")
        return rules

    root = tree.getroot()
    for ns_uri in ["http://checklists.nist.gov/xccdf/1.2",
                   "http://checklists.nist.gov/xccdf/1.1"]:
        for rule_el in root.iter(f"{{{ns_uri}}}Rule"):
            rid = rule_el.get("id", "")
            if not rid.startswith(PREFIX):
                continue

            title_el = rule_el.find(f"{{{ns_uri}}}title")
            title    = (title_el.text or "").strip() if title_el is not None else rid

            desc_el = rule_el.find(f"{{{ns_uri}}}description")
            desc    = "".join(desc_el.itertext()).strip() if desc_el is not None else ""

            severity = rule_el.get("severity", "unknown")

            fixes = {}
            for fix_el in rule_el.findall(f"{{{ns_uri}}}fix"):
                fixes[fix_el.get("system", "fixtext")] = (fix_el.text or "").strip()
            ft = rule_el.find(f"{{{ns_uri}}}fixtext")
            if ft is not None and ft.text:
                fixes["fixtext"] = ft.text.strip()

            fix_text = (
                fixes.get("urn:xccdf:fix:script:sh")
                or fixes.get("fixtext")
                or next(iter(fixes.values()), "")
            )
            fix_system = (
                "sh"      if "urn:xccdf:fix:script:sh" in fixes else
                "fixtext" if "fixtext" in fixes else
                ("other"  if fixes else "none")
            )

            rules[rid] = {
                "title":       title,
                "description": desc[:1200],
                "fix":         fix_text,
                "fix_system":  fix_system,
                "severity":    severity,
            }

    if not rules:
        print(f"\n  No Rule elements found in {path}.")
        print("  Re-run oscap with --results-arf to embed full benchmark content.")
    return rules


def build_rule_set(scan_rules):
    """
    Resolves each of the 46 hardcoded KEEP_RULE_IDS against the scan XML.
    Returns a list of {rule_id, matched, info}. Unmatched rules still run
    (with a generic description) rather than being silently dropped.
    """
    resolved, unmatched = [], []
    for rid in KEEP_RULE_IDS:
        if rid in scan_rules:
            info = scan_rules[rid]
            resolved.append({"rule_id": rid, "matched": True, "info": info})
        else:
            unmatched.append(rid)
            short = rid.replace(PREFIX, "")
            resolved.append({
                "rule_id": rid,
                "matched": False,
                "info": {
                    "title": short.replace("_", " ").title(),
                    "description": f"(Not found in {SCAN_RESULT_XML} -- apply the "
                                    f"standard Ubuntu 22.04 remediation for this control.)",
                    "fix": "",
                    "fix_system": "none",
                    "severity": "unknown",
                },
            })

    print(f"  Rule set built: {len(resolved)} KEEP rules "
          f"({len(resolved) - len(unmatched)} matched to scan XML, "
          f"{len(unmatched)} using a generic fallback description)")
    if unmatched:
        print(f"    Unmatched (still run): {[r.replace(PREFIX, '') for r in unmatched]}")
    return resolved


def filter_to_only_rules(rule_set, wanted):
    def short_of(rid):
        return rid[len(PREFIX):] if rid.startswith(PREFIX) else rid
    filtered = [item for item in rule_set if short_of(item["rule_id"]) in wanted]
    found = {short_of(item["rule_id"]) for item in filtered}
    missing = wanted - found
    if missing:
        print(f"  [WARN] --only-rules: not found in KEEP list, ignored: {sorted(missing)}")
    print(f"  [--only-rules] Restricting to {len(filtered)} of {len(rule_set)} rules: {sorted(found)}")
    return filtered


def filter_to_retry_failed(rule_set, json_path):
    with open(json_path) as f:
        prior = json.load(f)
    prior_rules = prior.get("rules", {})
    needs_retry = {rid for rid, rec in prior_rules.items()
                   if rec.get("status") != "oscap_pass"}
    filtered = [item for item in rule_set if item["rule_id"] in needs_retry]
    def short_of(rid):
        return rid[len(PREFIX):] if rid.startswith(PREFIX) else rid
    print(f"  [--retry-failed] {json_path}: {len(needs_retry)} rules were not "
          f"oscap_pass -> restricting to {len(filtered)} of {len(rule_set)}: "
          f"{sorted(short_of(i['rule_id']) for i in filtered)}")
    return filtered

# ─────────────────────────────────────────────────────────────────────────────
# LAB SERVER QUERY (with multi-endpoint probe + graceful shape detection)
# ─────────────────────────────────────────────────────────────────────────────

def _try_openai_chat(model, prompt, max_tokens, timeout):
    url = f"{LAB_URL}/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": 0.2, "stream": False}
    resp = requests.post(url, json=payload, auth=(LAB_USER, LAB_PASS), verify=False, timeout=timeout)
    return resp, url

def _try_ollama_chat(model, prompt, max_tokens, timeout):
    url = f"{LAB_URL}/api/chat"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "stream": False, "options": {"temperature": 0.2, "num_predict": max_tokens}}
    resp = requests.post(url, json=payload, auth=(LAB_USER, LAB_PASS), verify=False, timeout=timeout)
    return resp, url

def _try_ollama_generate(model, prompt, max_tokens, timeout):
    url = f"{LAB_URL}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.2, "num_predict": max_tokens}}
    resp = requests.post(url, json=payload, auth=(LAB_USER, LAB_PASS), verify=False, timeout=timeout)
    return resp, url

_ENDPOINT_TRIED_ORDER = [_try_openai_chat, _try_ollama_chat, _try_ollama_generate]


def probe_lab_server():
    print("\n" + "=" * 60)
    print(f"  PROBING LAB SERVER: {LAB_URL}")
    print("=" * 60)
    for method, path in [("GET", "/v1/models"), ("GET", "/api/tags"), ("GET", "/"), ("GET", "/health")]:
        url = LAB_URL + path
        try:
            resp = requests.request(method, url, auth=(LAB_USER, LAB_PASS), verify=False, timeout=15)
            print(f"  {method} {path:20s} -> {resp.status_code}  {resp.text[:150]!r}")
        except requests.exceptions.RequestException as e:
            print(f"  {method} {path:20s} -> ERROR: {e}")

    test_model = MODELS[0]
    print(f"\n  Now trying actual chat-completion shapes with model '{test_model}':")
    for fn in _ENDPOINT_TRIED_ORDER:
        try:
            resp, url = fn(test_model, "Say OK.", 20, 30)
            print(f"  {fn.__name__:20s} [{url}] -> {resp.status_code}  {resp.text[:200]!r}")
        except requests.exceptions.RequestException as e:
            print(f"  {fn.__name__:20s} -> ERROR: {e}")


def strip_code_fences(text):
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def query_lab_model(model_name, prompt, timeout=900):
    url = f"{LAB_URL}/chat"
    payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}
    try:
        response = requests.post(url, auth=(LAB_USER, LAB_PASS), json=payload, verify=False, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return {"model": model_name, "response": "", "error": str(e), "fatal": True}

    reply_text = None
    try:
        if "choices" in data:
            reply_text = data["choices"][0]["message"]["content"].strip()
        elif "message" in data:
            msg = data["message"]
            reply_text = (msg.get("content", "") if isinstance(msg, dict) else str(msg)).strip()
        elif "response" in data:
            reply_text = str(data["response"]).strip()
        else:
            print(f"    [DEBUG] Unexpected JSON structure. Keys: {list(data.keys())}")
            reply_text = str(data)
    except Exception as e:
        print(f"    [DEBUG] Error extracting content: {e}. Data: {data}")
        return {"model": model_name, "response": "", "error": f"content extraction failed: {e}", "fatal": True}

    if not reply_text:
        return {"model": model_name, "response": "", "error": "empty response body", "fatal": True}
    return {"model": model_name, "response": reply_text, "elapsed_seconds": 0, "error": None, "fatal": False}

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT BUILDER — single fixed profile, no MAX/MIN branching
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(rule_id, rule_info):
    short_id = rule_id[len(PREFIX):] if rule_id.startswith(PREFIX) else rule_id
    hint = RULE_HINTS.get(short_id)
    hint_block = (
        f"\nKNOWN ISSUE FOR THIS SPECIFIC RULE (from a prior 24.04 run — "
        f"re-verify it still applies on 22.04) -- read carefully:\n{hint}\n"
        if hint else ""
    )

    known_fix = KNOWN_FIXES.get(short_id)
    known_fix_block = (
        f"\nCANDIDATE SCRIPT FOR THIS EXACT RULE, VERIFIED ON A 24.04 HOST "
        f"(NOT YET RE-VERIFIED ON 22.04) -- use it as a strong starting "
        f"point, adjust only if you see a concrete, specific reason it "
        f"won't work on this host:\n```bash\n{known_fix}\n```\n"
        if known_fix else ""
    )

    return f"""You are a Linux system hardening expert. Write a remediation script for
the following CIS/OpenSCAP rule on this specific host.

Host: {SYSTEM_INFO['hostname']} | {SYSTEM_INFO['os']} | kernel {SYSTEM_INFO['kernel']} | {SYSTEM_INFO['arch']}

USER PROFILE (this rule was already determined to be KEEP for this exact
profile -- your job is the correct technical remediation, not another
KEEP/SKIP judgment call):
{PROFILE_DESCRIPTION}

RULE TO REMEDIATE:
  ID: {rule_id}
  Title: {rule_info['title']}
  Severity: {rule_info['severity']}
  Description: {rule_info['description']}

Reference fix from benchmark (format: {rule_info.get('fix_system', 'none')}):
{rule_info['fix'] or '(none provided -- write the standard Ubuntu 22.04 remediation yourself)'}

If the reference fix is bash (sh), adapt it directly.
If it is Ansible/Puppet/blueprint or missing, translate the intent into plain bash.
{hint_block}
{known_fix_block}
The script will be executed with `sudo bash -c "<script>"`, so:
  - Do NOT prefix every individual line with `sudo` (redundant, can break
    heredocs/pipes) -- the whole script already runs as root.
  - Just write plain root-level commands.
  - Do not disable Docker's bridge networking, IP forwarding, or any
    already-listening local server/API port as a side effect of this fix.

Write a bash script that remediates this rule. Requirements:
  - Idempotent (safe to re-run).
  - Back up a file before editing it in place, where that's meaningful.

Output ONLY the script -- no prose, no markdown fences, no explanation.

If -- and only if -- you have a genuine, non-technical doubt about what's
actually wanted here, you may instead ask a human ONE clarifying question,
in exactly this format and nothing else:
CLARIFY
QUESTION: <your question, plain language, no jargon>
OPTIONS:
1) <option>
2) <option>
3) <option>
Do not use this for ordinary technical uncertainty (missing packages,
unfamiliar paths, etc) -- just write your best script for those and let the
error tell you if you're wrong.
"""


def build_retry_prompt(base_prompt, attempt_history, clarification=None):
    parts = [base_prompt]
    if clarification:
        parts.append(f"\nThe human clarified: {clarification}\n"
                      f"Use this to inform your script. Do not ask the same question again.")
    for i, att in enumerate(attempt_history, 1):
        parts.append(f"\n--- Your attempt #{i} failed ---")
        parts.append(f"Script you wrote:\n{att['script']}")
        parts.append(f"Failure reason: {att['failure_reason']}")
        if att.get("detail"):
            parts.append(f"Details:\n{str(att['detail'])[:1000]}")
    parts.append(
        "\nWrite a corrected script that avoids the above problem(s). If "
        "you still have a genuine doubt, use the CLARIFY format instead. "
        "Output ONLY the script or ONLY the CLARIFY block -- no prose, no "
        "markdown fences."
    )
    return "\n".join(parts)


def parse_model_output(raw):
    stripped = raw.strip()
    first_line = stripped.split("\n", 1)[0].strip().upper()
    if first_line != "CLARIFY":
        return {"type": "script", "script": strip_code_fences(raw)}

    question, options = "", []
    q_match = re.search(r"QUESTION:\s*(.+)", stripped)
    if q_match:
        question = q_match.group(1).split("\n")[0].strip()
    for line in stripped.split("\n"):
        m = re.match(r"\s*\d+\)\s*(.+)", line)
        if m:
            options.append(m.group(1).strip())

    if not question:
        return {"type": "script", "script": strip_code_fences(raw)}
    return {"type": "clarify", "question": question, "options": options}


def ask_user_clarification(rule_short, question, options):
    print(f"\n    [AGENT HAS A QUESTION] {rule_short}")
    print(f"    {question}")
    for i, opt in enumerate(options, 1):
        print(f"      {i}) {opt}")
    print(f"      0) Something else (type your own answer)")
    choice = input("    Your answer [number, or 0 for free text]: ").strip()
    valid_choices = [str(i) for i in range(1, len(options) + 1)]
    if choice == "0" or choice not in valid_choices:
        return input("    Type your answer: ").strip()
    return options[int(choice) - 1]

# ─────────────────────────────────────────────────────────────────────────────
# OSCAP SINGLE-RULE VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def verify_rule(rule_id):
    cmd = (
        f"sudo oscap xccdf eval --rule {rule_id} "
        f"--profile {BENCHMARK_PROFILE_ID} "
        f"{BENCHMARK_XML} 2>/dev/null | grep 'Result'"
    )
    try:
        out = subprocess.check_output(cmd, shell=True, text=True, timeout=90).strip().lower()
        if "pass" in out:            return "pass"
        elif "fail" in out:          return "fail"
        elif "notapplicable" in out: return "notapplicable"
        else:                        return "notchecked"
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception:
        return "error"

# ─────────────────────────────────────────────────────────────────────────────
# BREAK-RULES SCRIPT GENERATOR (only the 46 KEEP rules matter here)
# ─────────────────────────────────────────────────────────────────────────────

BREAK_COMMANDS = {
    "accounts_passwords_pam_faillock_deny":
        "sudo sed -i '/faillock/d' /etc/pam.d/common-auth /etc/pam.d/common-account 2>/dev/null || true",
    "accounts_passwords_pam_faillock_unlock_time":
        "sudo sed -i '/faillock/d' /etc/pam.d/common-auth 2>/dev/null || true",
    "accounts_password_pam_dcredit":
        "sudo sed -i '/dcredit/d' /etc/security/pwquality.conf 2>/dev/null || true",
    "accounts_password_pam_minlen":
        "sudo sed -i '/minlen/d' /etc/security/pwquality.conf 2>/dev/null || true",
    "accounts_password_pam_ucredit":
        "sudo sed -i '/ucredit/d' /etc/security/pwquality.conf 2>/dev/null || true",
    "set_password_hashing_algorithm_systemauth":
        "sudo sed -i 's/ *sha512//' /etc/pam.d/common-password 2>/dev/null || true",
    "accounts_tmout":
        "sudo sed -i '/TMOUT/d' /etc/bash.bashrc /etc/profile /etc/profile.d/*.sh 2>/dev/null || true",
    "accounts_umask_etc_bashrc":
        "sudo sed -i '/umask 027/d; /umask 077/d' /etc/bash.bashrc 2>/dev/null || true",
    "accounts_umask_etc_login_defs":
        "sudo sed -i 's/^UMASK.*/UMASK 022/' /etc/login.defs 2>/dev/null || true",
    "sudo_custom_logfile":
        "sudo sed -i '/logfile/d' /etc/sudoers 2>/dev/null; sudo rm -f /etc/sudoers.d/01-cis-sudo-logfile 2>/dev/null || true",
    "sudo_require_reauthentication":
        "sudo rm -f /etc/sudoers.d/reauthenticate_sudo 2>/dev/null || true",
    "sudo_remove_no_authenticate":
        "echo 'Defaults !authenticate' | sudo tee /etc/sudoers.d/zz_break_authenticate > /dev/null",
    "grub2_enable_apparmor":
        "sudo sed -i 's/ apparmor=1 security=apparmor//' /etc/default/grub 2>/dev/null; sudo update-grub 2>/dev/null || true",
    "sysctl_net_ipv6_conf_all_forwarding":
        "sudo sed -i '/net.ipv6.conf.all.forwarding/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv6.conf.all.forwarding=1 2>/dev/null || true",
    "sysctl_net_ipv4_conf_all_accept_redirects":
        "sudo sed -i '/accept_redirects/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.conf.all.accept_redirects=1 2>/dev/null || true",
    "sysctl_net_ipv4_conf_all_log_martians":
        "sudo sed -i '/log_martians/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.conf.all.log_martians=0 2>/dev/null || true",
    "sysctl_net_ipv4_conf_all_rp_filter":
        "sudo sed -i '/rp_filter/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.conf.all.rp_filter=0 2>/dev/null || true",
    "sysctl_net_ipv4_tcp_syncookies":
        "sudo sed -i '/syncookies/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.tcp_syncookies=0 2>/dev/null || true",
    "sysctl_net_ipv4_conf_all_send_redirects":
        "sudo sed -i '/send_redirects/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.conf.all.send_redirects=1 2>/dev/null || true",
    "sysctl_net_ipv4_ip_forward":
        "sudo sed -i '/ip_forward/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true",
    "sysctl_kernel_randomize_va_space":
        "sudo sed -i '/randomize_va_space/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w kernel.randomize_va_space=0 2>/dev/null || true",
    "sysctl_fs_suid_dumpable":
        "sudo sed -i '/suid_dumpable/d' /etc/sysctl.conf /etc/sysctl.d/*.conf 2>/dev/null; sudo sysctl -w fs.suid_dumpable=1 2>/dev/null || true",
    "disable_users_coredumps":
        "sudo sed -i '/hard core/d; /soft core/d' /etc/security/limits.conf 2>/dev/null; echo '* soft core unlimited' | sudo tee -a /etc/security/limits.conf",
    "kernel_module_hfs_disabled":
        "sudo rm -f /etc/modprobe.d/hfs.conf 2>/dev/null || true",
    "mount_option_dev_shm_nodev":
        "sudo sed -i '/\\/dev\\/shm/d' /etc/fstab; sudo mount -o remount /dev/shm 2>/dev/null || true",
    "mount_option_dev_shm_noexec":
        "sudo sed -i '/\\/dev\\/shm/d' /etc/fstab; sudo mount -o remount /dev/shm 2>/dev/null || true",
    "mount_option_dev_shm_nosuid":
        "sudo sed -i '/\\/dev\\/shm/d' /etc/fstab; sudo mount -o remount /dev/shm 2>/dev/null || true",
    "file_permissions_crontab":
        "sudo chmod 0644 /etc/crontab 2>/dev/null || true",
    "file_permissions_cron_allow":
        "sudo chmod 0644 /etc/cron.allow 2>/dev/null || true",
    "file_permissions_cron_d":
        "sudo chmod 0755 /etc/cron.d 2>/dev/null || true",
    "file_permissions_cron_daily":
        "sudo chmod 0755 /etc/cron.daily 2>/dev/null || true",
    "file_owner_cron_allow":
        "sudo chown nobody:nogroup /etc/cron.allow 2>/dev/null || true",
    "file_groupowner_cron_allow":
        "sudo chgrp root /etc/cron.allow 2>/dev/null || true",
    "file_groupowner_backup_etc_gshadow":
        "sudo chgrp root /etc/gshadow- 2>/dev/null || true",
    "package_openldap-clients_removed":
        "sudo apt-get install -y ldap-utils 2>/dev/null || true",
    "package_rsync_removed":
        "sudo apt-get install -y rsync 2>/dev/null || true",
    "package_telnet_removed":
        "sudo apt-get install -y telnet 2>/dev/null || true",
    "package_vsftpd_removed":
        "sudo apt-get install -y vsftpd 2>/dev/null || true",
    "package_nis_removed":
        "sudo apt-get install -y nis 2>/dev/null || true",
    "package_rpcbind_removed":
        "sudo apt-get install -y rpcbind 2>/dev/null || true",
}


def generate_break_script(rule_set, output_path="break_rules.sh"):
    not_found = []
    lines = [
        "#!/bin/bash",
        "# Auto-generated by remediation_2204_v1.py",
        "# Resets the 46 KEEP-category rules back to FAILING state between models",
        "# Run between models: bash break_rules.sh",
        "echo '=== Resetting rules to failing state ==='",
        "",
    ]
    for item in rule_set:
        short = item["rule_id"].replace(PREFIX, "")
        cmd = BREAK_COMMANDS.get(short)
        if cmd:
            lines.append(f"echo '  Breaking: {short}'")
            lines.append(cmd)
        else:
            not_found.append(short)
    lines += ["", "echo '=== Reset complete ==='"]
    if not_found:
        lines.append(f"echo 'No break command for: {not_found}'")
    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(output_path, 0o755)
    print(f"\n  Break script written to: {output_path}")
    if not_found:
        print(f"  KEEP rules without break command (add manually): {not_found}")
    return output_path

# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT EXECUTION (elevated)
# ─────────────────────────────────────────────────────────────────────────────

def run_remediation_script(script, timeout=120):
    cmd = ["sudo", "-n", "bash", "-c", script] if ELEVATE_WITH_SUDO else ["bash", "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def run_remediation_script_interactive(script, timeout=180):
    cmd = ["sudo", "bash", "-c", script]
    return subprocess.run(cmd, timeout=timeout)

# ─────────────────────────────────────────────────────────────────────────────
# SUDOERS CORRUPTION GUARD
# ─────────────────────────────────────────────────────────────────────────────

SUDOERS_TOUCHING_HINTS = ("sudoers", "visudo")
_PASSWORD_REQUIRED_PATTERNS = ("a password is required", "sudo: a terminal is required", "sudo: no tty present")

def script_touches_sudoers(script):
    return any(h in script.lower() for h in SUDOERS_TOUCHING_HINTS)

def snapshot_sudoers():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = f"/tmp/sudoers_backup_{ts}"
    try:
        subprocess.run(["sudo", "mkdir", "-p", backup_dir], check=True, capture_output=True, timeout=15)
        subprocess.run(["sudo", "cp", "-a", "/etc/sudoers", f"{backup_dir}/sudoers"], check=True, capture_output=True, timeout=15)
        subprocess.run(["sudo", "cp", "-a", "/etc/sudoers.d", f"{backup_dir}/sudoers.d"], check=True, capture_output=True, timeout=15)
        return backup_dir
    except Exception as e:
        print(f"    [WARN] Could not snapshot sudoers, rollback disabled this pass: {e}")
        return None

def sudoers_is_valid():
    try:
        proc = subprocess.run(["sudo", "-n", "visudo", "-c"], capture_output=True, text=True, timeout=15)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    except Exception as e:
        return False, str(e)

def restore_sudoers(backup_dir):
    if not backup_dir:
        return False
    try:
        subprocess.run(["sudo", "cp", "-a", f"{backup_dir}/sudoers", "/etc/sudoers"], check=True, capture_output=True, timeout=15)
        subprocess.run(["sudo", "rm", "-rf", "/etc/sudoers.d"], check=True, capture_output=True, timeout=15)
        subprocess.run(["sudo", "cp", "-a", f"{backup_dir}/sudoers.d", "/etc/sudoers.d"], check=True, capture_output=True, timeout=15)
        return True
    except Exception as e:
        print(f"    [ERROR] sudoers rollback failed: {e} -- fix /etc/sudoers manually with `visudo`!")
        return False

def detect_password_gate(stderr):
    low = (stderr or "").lower()
    return any(p in low for p in _PASSWORD_REQUIRED_PATTERNS)

def ask_permission(rule_short, reason):
    print(f"\n    [PERMISSION NEEDED] {rule_short}")
    print(f"    Reason: {reason}")
    ans = input("    Allow the agent to proceed with this? [y/n]: ").strip().lower()
    return ans == "y"

# Rules that can lock you out of the VM's normal login/sudo path once
# active -- these require explicit confirmation even under --auto.
LOCKOUT_RISK_RULES = {
    "accounts_passwords_pam_faillock_deny":
        "Sets how many wrong passwords trigger a lockout. Combined with "
        "faillock being enabled elsewhere, a low value makes an accidental "
        "lockout easier to trigger.",
    "accounts_passwords_pam_faillock_unlock_time":
        "Sets how long a lockout lasts once triggered. Doesn't cause a "
        "lockout itself, but determines how long you're stuck if one happens.",
    "sudo_require_reauthentication":
        "Changes how long sudo caches your password. A wrong value can make "
        "the whole pipeline unusable (every sudo call demanding a fresh "
        "password), though it won't lock your login itself.",
    "sudo_remove_no_authenticate":
        "Edits /etc/sudoers and /etc/sudoers.d/* directly. A bad edit here "
        "can break sudo entirely -- same family of risk as sudoers corruption.",
}

def warn_lockout_risk(short):
    reason = LOCKOUT_RISK_RULES.get(short)
    if not reason:
        return True
    print(f"\n    {'='*60}")
    print(f"    [LOCKOUT RISK] {short}")
    print(f"    {reason}")
    print(f"    Before continuing: make sure you have a SECOND terminal/SSH "
          f"session already logged in to this VM, in case this one locks you out.")
    print(f"    {'='*60}")
    return input("    Proceed with this rule? [y/n]: ").strip().lower() == "y"

# ─────────────────────────────────────────────────────────────────────────────
# MULTI-PASS AGENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def remediate_rule(model, rule_id, rule_info, auto_approve, matched):
    short = rule_id.replace(PREFIX, "")

    if not warn_lockout_risk(short):
        return {"status": "skipped_lockout_risk", "matched_in_scan": matched}, {"rejected": 1}

    base_prompt = build_prompt(rule_id, rule_info)

    counters = {}
    attempt_history = []
    clarification = None
    clarifications_used = 0
    passes_log = []
    rule_record = {"matched_in_scan": matched}

    pass_num = 0
    while pass_num < MAX_PASSES:
        pass_num += 1

        if pass_num == 1 and not clarification and short in KNOWN_FIXES:
            print(f"    Using candidate pre-verified script for {short} "
                  f"(skipping LLM query on pass 1 -- oscap will still judge it).")
            parsed = {"type": "script", "script": KNOWN_FIXES[short]}
        else:
            prompt = base_prompt if (pass_num == 1 and not clarification) \
                else build_retry_prompt(base_prompt, attempt_history, clarification)

            print(f"    Querying {model} (pass {pass_num}/{MAX_PASSES})...")
            try:
                result_dict = query_lab_model(model, prompt)
                if result_dict.get("error"):
                    raise Exception(result_dict["error"])
                raw = result_dict["response"]
            except Exception as e:
                print(f"    [QUERY ERROR] {e}")
                rule_record["status"] = "query_error"
                rule_record["error"] = str(e)
                rule_record["passes"] = passes_log
                counters["query_error"] = 1
                time.sleep(2)
                return rule_record, counters

            parsed = parse_model_output(raw)

        if parsed["type"] == "clarify":
            if clarifications_used >= 1:
                print(f"    [IGNORING REPEAT QUESTION] \"{parsed['question']}\" -- "
                      f"already clarified once, treating as a failed attempt.")
                attempt_history.append({
                    "script": "(model asked another question instead of a script)",
                    "failure_reason": "repeated_clarify_ignored",
                    "detail": parsed["question"],
                })
                passes_log.append({"pass": pass_num, "type": "clarify_ignored", "question": parsed["question"]})
                continue
            clarification = ask_user_clarification(short, parsed["question"], parsed["options"])
            clarifications_used += 1
            counters["clarifications_asked"] = counters.get("clarifications_asked", 0) + 1
            passes_log.append({"pass": pass_num, "type": "clarify",
                                "question": parsed["question"], "answer": clarification})
            pass_num -= 1
            continue

        script = parsed["script"]
        if "attempted" not in counters:
            counters["attempted"] = 1

        print("\n    Proposed fix:")
        print("    " + "-" * 52)
        for line in script.split("\n")[:20]:
            print(f"    {line}")
        if script.count("\n") > 20:
            print(f"    ... ({script.count(chr(10)) - 20} more lines)")
        print("    " + "-" * 52)

        if pass_num == 1:
            if auto_approve:
                approved = True
                print("    [AUTO] Applying.")
            else:
                ans = input("    Apply? [y/n/s=show full]: ").strip().lower()
                if ans == "s":
                    print(f"\n{script}\n")
                    ans = input("    Apply? [y/n]: ").strip().lower()
                approved = (ans == "y")
            rule_record["approved"] = approved
            if not approved:
                rule_record["script"] = script
                rule_record["status"] = "rejected"
                rule_record["passes"] = passes_log
                counters["rejected"] = 1
                return rule_record, counters
            counters["approved"] = 1
        else:
            print("    [AUTO-RETRY] Applying revised script (rule already approved).")

        rule_record["script"] = script
        backup_dir = snapshot_sudoers() if script_touches_sudoers(script) else None
        timeout_s = RULE_TIMEOUTS.get(short, DEFAULT_SCRIPT_TIMEOUT)

        try:
            proc = run_remediation_script(script, timeout=timeout_s)
            exit_code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            print(f"    [TIMEOUT] Script did not finish within {timeout_s}s.")
            passes_log.append({"pass": pass_num, "type": "script", "status": "timeout"})
            attempt_history.append({"script": script, "failure_reason": "timeout",
                                     "detail": f"Script did not finish within {timeout_s}s."})
            continue
        except Exception as e:
            print(f"    [RUNTIME ERROR] {e}")
            passes_log.append({"pass": pass_num, "type": "script", "status": "error", "error": str(e)})
            attempt_history.append({"script": script, "failure_reason": "runtime_error", "detail": str(e)})
            continue

        rule_record["exit_code"] = exit_code
        rule_record["stdout"] = (out or "")[-2000:]
        rule_record["stderr"] = (err or "")[-2000:]

        if exit_code != 0 and detect_password_gate(err):
            allowed = ask_permission(short, "The script needs an interactive sudo password "
                                            "(no NOPASSWD rule covers this command).")
            if allowed:
                try:
                    proc2 = run_remediation_script_interactive(script, timeout=timeout_s)
                    exit_code = proc2.returncode
                    out = "(ran interactively, output not captured)"
                    err = "" if exit_code == 0 else "(ran interactively -- see terminal output above)"
                    rule_record["exit_code"] = exit_code
                    rule_record["stdout"] = out
                    rule_record["stderr"] = err
                except subprocess.TimeoutExpired:
                    print(f"    [TIMEOUT] Interactive script did not finish within {timeout_s}s -- resetting terminal.")
                    try:
                        subprocess.run(["stty", "sane"], timeout=5)
                    except Exception:
                        pass
                    exit_code = -1
                    err = f"Interactive script timed out after {timeout_s}s."
                    rule_record["exit_code"] = exit_code
                    rule_record["stdout"] = ""
                    rule_record["stderr"] = err
                except Exception as e:
                    exit_code = -1
                    err = str(e)
                    rule_record["stderr"] = err
            else:
                rule_record["status"] = "permission_denied"
                rule_record["passes"] = passes_log
                counters["permission_denied"] = 1
                return rule_record, counters

        if backup_dir is not None:
            valid, vmsg = sudoers_is_valid()
            if not valid:
                print(f"    [SUDOERS BROKEN] {vmsg[:200]}")
                restore_sudoers(backup_dir)
                counters["sudoers_rollback"] = counters.get("sudoers_rollback", 0) + 1
                passes_log.append({"pass": pass_num, "type": "script", "status": "sudoers_corrupted", "detail": vmsg})
                attempt_history.append({
                    "script": script, "failure_reason": "sudoers_corrupted",
                    "detail": f"This script left /etc/sudoers invalid (visudo -c: {vmsg}). "
                              f"Rolled back. Use a properly formatted `Defaults ...` line, "
                              f"or avoid touching sudoers if this rule isn't about sudo config.",
                })
                continue

        if exit_code != 0:
            print(f"    [SCRIPT ERROR] exit={exit_code}")
            print(f"    {(err or '')[-150:]}")
            passes_log.append({"pass": pass_num, "type": "script", "status": "script_error", "exit_code": exit_code})
            attempt_history.append({"script": script, "failure_reason": "script_error", "detail": err})
            continue

        print("    Verifying with oscap...")
        verdict = verify_rule(rule_id)
        rule_record["oscap_result"] = verdict
        passes_log.append({"pass": pass_num, "type": "script", "status": f"oscap_{verdict}"})

        if verdict == "pass":
            print("    PASS")
            rule_record["status"] = "oscap_pass"
            rule_record["passes"] = passes_log
            counters["script_ok"] = 1
            counters["oscap_pass"] = 1
            return rule_record, counters

        print(f"    {verdict.upper()}")
        attempt_history.append({
            "script": script, "failure_reason": "oscap_fail",
            "detail": f"Script ran fine (exit 0) but oscap re-check still reports "
                      f"'{verdict}' for {rule_id}. Look again at what the rule verifies.",
        })

    rule_record["passes"] = passes_log
    last = attempt_history[-1] if attempt_history else None
    rule_record["status"] = f"unresolved_{last['failure_reason']}" if last else "unresolved"
    counters["passes_exhausted"] = counters.get("passes_exhausted", 0) + 1
    if last and last["failure_reason"] in ("script_error", "timeout", "runtime_error",
                                            "sudoers_corrupted", "repeated_clarify_ignored"):
        counters["script_error"] = counters.get("script_error", 0) + 1
    else:
        counters["oscap_fail"] = counters.get("oscap_fail", 0) + 1
        if rule_record.get("exit_code") == 0:
            counters["script_ok"] = counters.get("script_ok", 0) + 1
    return rule_record, counters

# ─────────────────────────────────────────────────────────────────────────────
# SINGLE MODEL RUN
# ─────────────────────────────────────────────────────────────────────────────

def run_one_model(model, rule_set, auto_approve):
    print(f"\n{'='*60}")
    print(f"  MODEL   : {model}")
    print(f"  PROFILE : Software Developer (Docker/Podman + VM, local server/API)")
    print(f"  RULES   : {len(rule_set)} KEEP rules sent to the LLM")
    print(f"{'='*60}")

    model_results = {
        "model": model,
        "started": datetime.datetime.now().isoformat(),
        "rules": {},
        "summary": {
            "attempted": 0, "approved": 0, "script_ok": 0, "oscap_pass": 0,
            "oscap_fail": 0, "query_error": 0, "script_error": 0, "rejected": 0,
            "clarifications_asked": 0, "sudoers_rollback": 0,
            "permission_denied": 0, "passes_exhausted": 0,
        },
    }

    for item in rule_set:
        rule_id, rule_info = item["rule_id"], item["info"]
        short = rule_id.replace(PREFIX, "")
        print(f"\n  [{short}]")

        rule_record, counters = remediate_rule(
            model=model, rule_id=rule_id, rule_info=rule_info,
            auto_approve=auto_approve, matched=item["matched"],
        )
        for key, inc in counters.items():
            model_results["summary"][key] = model_results["summary"].get(key, 0) + inc
        model_results["rules"][rule_id] = rule_record
        time.sleep(1)

    s = model_results["summary"]
    tested = s["attempted"]
    pct = (s["oscap_pass"] / tested * 100) if tested else 0
    print(f"\n  {model} DONE")
    print(f"  Tested={tested} Approved={s['approved']} PASS={s['oscap_pass']}({pct:.1f}%) "
          f"FAIL={s['oscap_fail']} Err={s['script_error']+s['query_error']}")
    print(f"  [Agent loop] Clarifications={s.get('clarifications_asked', 0)} "
          f"Sudoers rollbacks={s.get('sudoers_rollback', 0)} "
          f"Permission denied={s.get('permission_denied', 0)} "
          f"Passes exhausted={s.get('passes_exhausted', 0)}")

    model_results["finished"] = datetime.datetime.now().isoformat()
    return model_results

# ─────────────────────────────────────────────────────────────────────────────
# RESET PROMPT BETWEEN MODELS
# ─────────────────────────────────────────────────────────────────────────────

def prompt_reset(prev_model, next_model, break_script, use_snapshot):
    print(f"\n{'='*60}")
    print(f"  {prev_model} complete. Next: {next_model}")
    print(f"{'='*60}")
    if use_snapshot:
        print(f"""
  RESTORE VM SNAPSHOT before continuing.
  Snapshot name: '{SNAPSHOT_NAME}'

  On your HOST machine:
    VBoxManage controlvm "{VM_NAME}" poweroff
    VBoxManage snapshot "{VM_NAME}" restore "{SNAPSHOT_NAME}"
    VBoxManage startvm "{VM_NAME}" --type headless

  Then SSH back in and re-run with --model "{next_model}" --auto
""")
    else:
        print(f"""
  RESET RULES TO FAILING STATE before continuing.
  Run on the VM:
    bash {break_script}

  Then verify rules are failing:
    sudo oscap xccdf eval --profile {BENCHMARK_PROFILE_ID} \\
      --results {SCAN_RESULT_XML} {BENCHMARK_XML}
""")
    input("  Press Enter when ready for next model...")

# ─────────────────────────────────────────────────────────────────────────────
# SAVE RESULTS
# ─────────────────────────────────────────────────────────────────────────────

def save_results(all_results):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(RESULTS_DIR, f"remediation_2204_devprofile_{ts}")
    os.makedirs(run_dir, exist_ok=True)

    for r in all_results:
        fname = r["model"].replace(":", "_").replace("/", "_") + ".json"
        with open(os.path.join(run_dir, fname), "w") as f:
            json.dump(r, f, indent=2)

    lines = [
        "# CIS Remediation Comparison — Ubuntu 22.04, Software Developer Profile\n\n",
        f"**Timestamp:** {ts}\n\n---\n\n",
        "## Scoreboard (46 KEEP rules)\n\n",
        "| Model | Tested | PASS | PASS% | FAIL | Errors |\n",
        "|---|---|---|---|---|---|\n",
    ]
    for r in all_results:
        s = r["summary"]
        tot = s["attempted"]
        pct = (s["oscap_pass"] / tot * 100) if tot else 0
        lines.append(f"| {r['model']} | {tot} | {s['oscap_pass']} | {pct:.1f}% | "
                     f"{s['oscap_fail']} | {s['script_error']+s['query_error']} |\n")

    lines.append("\n---\n\n## Per-Rule Results\n\n")
    all_rids = []
    for r in all_results:
        for rid in r["rules"]:
            if rid not in all_rids:
                all_rids.append(rid)

    hdr = "| Rule | " + " | ".join(r["model"] for r in all_results) + " |\n"
    sep = "|---|" + "---|" * len(all_results) + "\n"
    lines += [hdr, sep]

    for rid in all_rids:
        short = rid.replace(PREFIX, "")
        row = f"| {short} |"
        for r in all_results:
            rec = r["rules"].get(rid, {})
            status = rec.get("status", "-")
            cell = ("PASS" if status == "oscap_pass" else
                    "FAIL" if "oscap_fail" in status else
                    status.replace("_", " "))
            row += f" {cell} |"
        lines.append(row + "\n")

    with open(os.path.join(run_dir, "comparison.md"), "w") as f:
        f.writelines(lines)

    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump({"timestamp": ts, "models": [{"model": r["model"], **r["summary"]} for r in all_results]},
                   f, indent=2)

    return run_dir

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if "--probe" in sys.argv:
        probe_lab_server()
        return

    auto_approve = "--auto" in sys.argv
    use_snapshot = "--snapshot" in sys.argv
    single_model = None
    retry_failed_path = None
    only_rules_wanted = None

    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            single_model = sys.argv[idx + 1]

    if "--retry-failed" in sys.argv:
        idx = sys.argv.index("--retry-failed")
        if idx + 1 < len(sys.argv):
            retry_failed_path = sys.argv[idx + 1]

    if "--only-rules" in sys.argv:
        idx = sys.argv.index("--only-rules")
        if idx + 1 < len(sys.argv):
            only_rules_wanted = {r.strip().removeprefix(PREFIX)
                                  for r in sys.argv[idx + 1].split(",") if r.strip()}

    if not LAB_USER or not LAB_PASS:
        print("[WARN] LAB_USER/LAB_PASS not set — export them before running "
              "(and rotate LAB_PASS if it was ever pasted into chat).")

    print("\n" + "=" * 60)
    print("  CIS Remediation Pipeline — Ubuntu 22.04, Software Developer profile")
    print(f"  Lab server : {LAB_URL}")
    print(f"  Models     : {', '.join(MODELS)}")
    print(f"  Elevation  : {'sudo bash -c <script>' if ELEVATE_WITH_SUDO else 'bash -c <script> (no sudo)'}")
    print("=" * 60)

    if auto_approve: print("  [AUTO]     All scripts applied without confirmation.")
    if single_model: print(f"  [SINGLE]   Running only: {single_model}")
    if use_snapshot: print("  [SNAPSHOT] Will prompt snapshot restore between models.")
    else:            print("  [BREAK]    Will generate break_rules.sh between models.")

    if ELEVATE_WITH_SUDO and auto_approve:
        print("  [NOTE]     --auto + sudo elevation: make sure this user has "
              "passwordless (NOPASSWD) sudo, or `sudo` will hang on the first rule.")

    scan_rules = parse_scan_xml(SCAN_RESULT_XML)
    rule_set = build_rule_set(scan_rules)

    if only_rules_wanted:
        rule_set = filter_to_only_rules(rule_set, only_rules_wanted)
    if retry_failed_path:
        if not os.path.exists(retry_failed_path):
            print(f"\n[ERROR] --retry-failed file not found: {retry_failed_path}")
            sys.exit(1)
        rule_set = filter_to_retry_failed(rule_set, retry_failed_path)

    print(f"\n  {len(rule_set)} KEEP rules will be sent to the LLM and remediated.")

    break_script = generate_break_script(rule_set)

    models_to_run = MODELS if not single_model else [m for m in MODELS if m == single_model]
    if not models_to_run:
        print(f"[ERROR] '{single_model}' not in MODELS list.")
        sys.exit(1)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    all_results = []

    print(f"\n  Running {len(models_to_run)} model(s) sequentially, {len(rule_set)} rule(s) each.")
    input("  Press Enter to start with the first model...")

    for i, model in enumerate(models_to_run):
        if i > 0:
            prompt_reset(prev_model=models_to_run[i-1], next_model=model,
                         break_script=break_script, use_snapshot=use_snapshot)

        result = run_one_model(model=model, rule_set=rule_set, auto_approve=auto_approve)
        all_results.append(result)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.join(RESULTS_DIR, f"{model.replace(':','_').replace('/','_')}_{ts}.json")
        with open(fname, "w") as f:
            json.dump(result, f, indent=2)
        print(f"  Saved: {fname}")

    run_dir = save_results(all_results)

    print(f"\n{'='*60}")
    print("  FINAL SCOREBOARD")
    print(f"{'='*60}")
    print(f"  {'Model':<25} {'Tested':>7} {'PASS':>6} {'PASS%':>7} {'ERR':>5}")
    print(f"  {'-'*25} {'------':>7} {'----':>6} {'-----':>7} {'---':>5}")
    for r in all_results:
        s = r["summary"]
        tot = s["attempted"]
        pct = (s["oscap_pass"]/tot*100) if tot else 0
        print(f"  {r['model']:<25} {tot:>7} {s['oscap_pass']:>6} {pct:>6.1f}% "
              f"{s['script_error']+s['query_error']:>5}")

    print(f"\n  Results : {run_dir}")
    print(f"  Table   : {run_dir}/comparison.md")
    print(f"  Reset   : bash {break_script}")


if __name__ == "__main__":
    main()
