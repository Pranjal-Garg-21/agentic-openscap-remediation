# CIS Analysis Comparison Report

**Role:** Personal Laptop / Home User

**Profile:**
- physical_access: Just me (Low risk of physical tampering)
- network_environment: Only trusted home/private networks (Standard firewall is fine)

**Total failed rules available:** 20
**Batch size:** 5 | **KEEP target:** 10

**Timestamp:** 20260629_135642

---

## Model: `mistralai/mistral-large-3-675b-instruct-2512`

**Batches used:** 4/4 | **Total time:** 14.8s

**KEEP: 10/10** target | SKIP: 10 | Unparsed: 0

### Kept rules

- **Build and Test AIDE Database** (`xccdf_org.ssgproject.content_rule_aide_build_database`, severity: medium)
  - Reason: File integrity monitoring (AIDE) is relevant for detecting unauthorized changes to critical files, even in a low-risk home environment. -
- **Configure Systemd Timer Execution of AIDE** (`xccdf_org.ssgproject.content_rule_aide_periodic_checking_systemd_timer`, severity: medium)
  - Reason: Periodic file integrity checks are relevant for maintaining security posture, even in a trusted home network. -
- **Set the UEFI Boot Loader Password** (`xccdf_org.ssgproject.content_rule_grub2_uefi_password`, severity: high)
  - Reason: Protecting bootloader settings with a password mitigates unauthorized physical access risks, even in a low-risk environment. -
- **Ensure journald is configured to compress large log files** (`xccdf_org.ssgproject.content_rule_journald_compress`, severity: medium)
  - Reason: Prevents disk exhaustion on a personal laptop where log retention is important.
- **Ensure journald is configured to write log files to persistent disk** (`xccdf_org.ssgproject.content_rule_journald_storage`, severity: medium)
  - Reason: Ensures logs persist across reboots on a personal laptop where log retention is important.
- **Disable systemd-journal-remote Socket** (`xccdf_org.ssgproject.content_rule_socket_systemd-journal-remote_disabled`, severity: medium)
  - Reason: Prevents unintended remote log reception on a trusted home network where no log server is needed.
- **Ensure Only One Firewall Service is Active** (`xccdf_org.ssgproject.content_rule_firewall_single_service_active`, severity: medium)
  - Reason: Ensures consistent firewall configuration, preventing rule conflicts or security gaps. Applies to all networked systems.
- **Verify Permissions on crontab** (`xccdf_org.ssgproject.content_rule_file_permissions_crontab`, severity: medium)
  - Reason: Prevents unauthorized modification of scheduled tasks, reducing privilege escalation risk. Applies to all Linux systems.
- **Uninstall rpcbind Package** (`xccdf_org.ssgproject.content_rule_package_rpcbind_removed`, severity: low)
  - Reason: RPC services like rpcbind are unnecessary on a personal laptop and pose a potential attack surface for remote exploits, even on trusted networks. -
- **Disable rpcbind Service** (`xccdf_org.ssgproject.content_rule_service_rpcbind_disabled`, severity: low)
  - Reason: Disabling unused RPC services reduces attack surface. Relevant even on trusted networks due to potential misconfigurations or zero-day exploits. -

### Raw batch responses

<details><summary>Batch 1 (4.2s, kept so far: 3)</summary>

```
```
- RULE ID: xccdf_org.ssgproject.content_rule_aide_build_database
  DECISION: KEEP
  REASON: File integrity monitoring (AIDE) is relevant for detecting unauthorized changes to critical files, even in a low-risk home environment.

- RULE ID: xccdf_org.ssgproject.content_rule_aide_periodic_checking_systemd_timer
  DECISION: KEEP
  REASON: Periodic file integrity checks are relevant for maintaining security posture, even in a trusted home network.

- RULE ID: xccdf_org.ssgproject.content_rule_partition_for_tmp
  DECISION: SKIP
  REASON: Separate /tmp partition is a hardening measure for multi-user or high-risk environments; low relevance for a single-user home laptop.

- RULE ID: xccdf_org.ssgproject.content_rule_grub2_uefi_password
  DECISION: KEEP
  REASON: Protecting bootloader settings with a password mitigates unauthorized physical access risks, even in a low-risk environment.

- RULE ID: xccdf_org.ssgproject.content_rule_service_systemd-journal-upload_enabled
  DECISION: SKIP
  REASON: Centralized log forwarding is unnecessary for a standalone home laptop with no requirement for remote log monitoring.
```
```

</details>

<details><summary>Batch 2 (3.1s, kept so far: 6)</summary>

```
```
RULE ID: xccdf_org.ssgproject.content_rule_journald_compress
DECISION: KEEP
REASON: Prevents disk exhaustion on a personal laptop where log retention is important.

RULE ID: xccdf_org.ssgproject.content_rule_journald_disable_forward_to_syslog
DECISION: SKIP
REASON: User environment does not use syslog; rule is irrelevant.

RULE ID: xccdf_org.ssgproject.content_rule_journald_forward_to_syslog
DECISION: SKIP
REASON: User environment does not use rsyslog; rule is irrelevant.

RULE ID: xccdf_org.ssgproject.content_rule_journald_storage
DECISION: KEEP
REASON: Ensures logs persist across reboots on a personal laptop where log retention is important.

RULE ID: xccdf_org.ssgproject.content_rule_socket_systemd-journal-remote_disabled
DECISION: KEEP
REASON: Prevents unintended remote log reception on a trusted home network where no log server is needed.
```
```

</details>

<details><summary>Batch 3 (3.2s, kept so far: 8)</summary>

```
```
RULE ID: xccdf_org.ssgproject.content_rule_systemd_journal_upload_server_tls
DECISION: SKIP
REASON: User environment is standalone personal laptop with no requirement for real-time remote log offloading. Rule is irrelevant to threat model.

RULE ID: xccdf_org.ssgproject.content_rule_systemd_journal_upload_url
DECISION: SKIP
REASON: User environment does not require remote log aggregation. Rule is irrelevant to threat model.

RULE ID: xccdf_org.ssgproject.content_rule_firewall_single_service_active
DECISION: KEEP
REASON: Ensures consistent firewall configuration, preventing rule conflicts or security gaps. Applies to all networked systems.

RULE ID: xccdf_org.ssgproject.content_rule_service_nftables_enabled
DECISION: SKIP
REASON: User environment does not specify nftables as the chosen firewall. Rule is conditional on firewall selection and not universally applicable.

RULE ID: xccdf_org.ssgproject.content_rule_file_permissions_crontab
DECISION: KEEP
REASON: Prevents unauthorized modification of scheduled tasks, reducing privilege escalation risk. Applies to all Linux systems.
```
```

</details>

<details><summary>Batch 4 (4.3s, kept so far: 10)</summary>

```
```
- RULE ID: xccdf_org.ssgproject.content_rule_package_nis_removed
  DECISION: SKIP
  REASON: NIS (Yellowpages) is not used in this environment. No threat model relevance for a personal laptop on trusted networks.

- RULE ID: xccdf_org.ssgproject.content_rule_package_rpcbind_removed
  DECISION: KEEP
  REASON: RPC services like rpcbind are unnecessary on a personal laptop and pose a potential attack surface for remote exploits, even on trusted networks.

- RULE ID: xccdf_org.ssgproject.content_rule_service_rpcbind_disabled
  DECISION: KEEP
  REASON: Disabling unused RPC services reduces attack surface. Relevant even on trusted networks due to potential misconfigurations or zero-day exploits.

- RULE ID: xccdf_org.ssgproject.content_rule_package_ypserv_removed
  DECISION: SKIP
  REASON: ypserv is part of NIS/YP, which is not used in this environment. No threat model relevance.

- RULE ID: xccdf_org.ssgproject.content_rule_service_ypserv_disabled
  DECISION: SKIP
  REASON: ypserv service is unrelated to this user's environment. No NIS/YP infrastructure present.
```
```

</details>

---

## Model: `microsoft/phi-4-mini-instruct`

**Batches used:** 4/4 | **Total time:** 65.6s

**KEEP: 0/10** target | SKIP: 0 | Unparsed: 20

### Kept rules

_None kept._

### Raw batch responses

<details><summary>Batch 1 (8.7s, kept so far: 0)</summary>

```
RULE for the and U
 5 for UEL: UEL to UEL from UEL from the and E (E: UEL: E (e and ELL: ELL: E: E
 1: E is and E (E: E (E (E: E: E: E and E: E: E (E be and E and E and E J and E and E: E: E: E (E (E (E from E (E from E: E: E1
  E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: "E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: " 1: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: E: 
 1: 1: " 1: E: " 1: ( 1 1: E: 1: 1: 1: E:  E:  and  (
: 1: 
```

</details>

<details><summary>Batch 2 (25.3s, kept so far: 0)</summary>

```
RULE 1, x, x, x.1, x: x, x, x, x, x, (x, x, reason, 3, x, x, x, x: x, and x, x, be, ( 
 2: 3, 1,, 1: 3, 1, and 5, 3, 5: 5: 1, 1, 1, 1, 1, 3, 1, 1, 1, 3: 1: 1,1: 4: 1, 1,1,1, 1, 1, 1, 1, 4, 1,1,1,1:1,1,1:1:1,1,1:1: 1, 1:1 and 1,1, 1,1:1, 1,1,1:1,1,1, 1, 1,1,1,1, 1, 1, 3:1,1,1:1: 3, 1, 3, 1: 1,1, 1, 1, 3, 3,1,1,1:1: 3:1,1,1,1: 3,1,1,1,1,1, 1, 1, 1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1, 1,1,1, 1,1,1,1,1, 1,1, 1,1
 1,1,1,1, 1:1,1, 1, 1, 1:1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1
 1,1,1
 1,1,1,1,1,1,1,1,1,1,1:1:1:1,1,1,1,1,1:1:1,1
 1,1,1,1,1,1,1,1,1:1,1,1
 1:1
 1,1,1,1,1,1,1,1,1,1,1,1,1:1:1:1:1,1:1:1:1:1:1,1:1:1,1:1:1,1:1:1,1,1:1,1,1:1:1,1:1,1,1:1,1,1:1:1,1,1:1:1,1,1:1:1,1:1:1:1:1:1:1:1,1,1,1,1:1,1:1:1:1:1,1:1:1
 1:1:1:1,1:1:1:1:1,1:1:1:1:1:1:1:1,1:1:1:1,1:1:1:1,1,1,1,1:1:1,1:1:1:1:1:1:1:1:1:1:1:1:1,1:1:1,1:1:1,1:1:1:1:1:1,1,1,1:1:1:1:1:1,1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1,1,1:1:1:1:1:1:1,1:1:1:1:1:1
 1:1:1,1:1:1:1,1,1:1:1:1:1:1:1:1,1:1:1:1:1:1:1:1
 1:1:1:1:1:1:1:11:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1,1,1:1:1:1:1:1:1:1:1:1,1,1:1:1,1,1,1,1:1
 1,1:11:1:1:1:1:11:1
 1,1
 1:1:1:1
 1:1:1:1:1:1:1:1,1:1:1:1,1,11,1:1:1,1:1:1:1:1:1,1,1,1,1,1:1,1,1:1:1,1:1:1:1:1:1:1,1:1:1:1:1:1:1:1:1:1:1:1:1:1,1:1:1:1:1:1,1:1:1:1:1:1:1:1,11:1:1:1:1:1:1,1:1:1:1:1:1:1:1:1:1,1,1,1,1,1,1:1:1:1:1 to log1111 to:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1 and 1:1:1:1:1:1:1:1,1
-1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1,1:1:1:1,1:1:1,1:1:1:1:1:1:1:1:1:1:1:1:1:1,1,1:1:1,1 and 1:1 and 1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1,1,1:1:1:1:1:1:1:1:1:1:1:1:1:1:1:1,1,1:1:1:1:
```

</details>

<details><summary>Batch 3 (26.2s, kept so far: 0)</summary>

```
RULE, 1, 1, 1, 1, 1, 1, 1, 1, 4, 1, 1, 1, 1, 2, 1, 1, 1,1, 1, 1, 4, 2, 1, 1,1, 1, 1, 1,1,1, 1,1,1, 1, 1, 1, 1,1, 1,1,1,1,1,1,1,1, and 1, 1, and keep, 1, and 1, 1,1,1,1, 1, 1, 1, 1,1,1, 1,1,1,1,1,1,1,1,1, 1, 1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1 1,1,1,1,1, 1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,11,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,11,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1: 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,11,1,1,1,1,1,1,1,1111,1,111,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1
 1,1,1,1,1x,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,11,1:1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,11,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1:1
 1,1,1,1:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1
```

</details>

<details><summary>Batch 4 (5.4s, kept so far: 0)</summary>

```
RULE: 5 and 5-5 (5 (5 (5.5,5,5, 5, and 5, 5, 4 and 5 (5 (5: 5 (5, 5: 5, 5, 5 (5, 5, 5, 5, 5, 5:5: 5, 5: 5 and 5 (5 (5, 5 (5, 5 (5 (5, 5: 5, 5, 5, 5, 5, 5, 5,5:5:5:5, 5, 5, 5, 5 5 5,5 (N-5 and 5,5 (5-5:5 (5, 5 (5 (5, 5,5,5,5, 5,5, 5:5,5:5,5,5,5, 5, 5, 5, 5, 5, 5, 5,5,5,5, 5,5 (5,5,5:5,5,5, 5,,,
```

</details>

---

