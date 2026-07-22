# CIS Remediation Comparison — Ubuntu 22.04, Software Developer Profile

**Timestamp:** 20260722_111501

---

## Scoreboard (46 KEEP rules)

| Model | Tested | PASS | PASS% | FAIL | Errors |
|---|---|---|---|---|---|
| qwen2.5:7b | 44 | 33 | 75.0% | 7 | 5 |

---

## Per-Rule Results

| Rule | qwen2.5:7b |
|---|---|
| partition_for_tmp | PASS |
| sudo_custom_logfile | PASS |
| sudo_remove_no_authenticate | query error |
| sudo_require_reauthentication | PASS |
| accounts_passwords_pam_faillock_deny | query error |
| accounts_passwords_pam_faillock_unlock_time | FAIL |
| accounts_password_pam_dcredit | PASS |
| accounts_password_pam_minlen | PASS |
| accounts_password_pam_ucredit | PASS |
| set_password_hashing_algorithm_systemauth | FAIL |
| accounts_umask_etc_bashrc | FAIL |
| accounts_umask_etc_login_defs | FAIL |
| accounts_tmout | PASS |
| grub2_enable_apparmor | FAIL |
| grub2_uefi_password | FAIL |
| journald_compress | PASS |
| journald_storage | PASS |
| sysctl_net_ipv6_conf_all_forwarding | PASS |
| sysctl_net_ipv4_conf_all_accept_redirects | PASS |
| sysctl_net_ipv4_conf_all_log_martians | PASS |
| sysctl_net_ipv4_conf_all_rp_filter | PASS |
| sysctl_net_ipv4_tcp_syncookies | PASS |
| sysctl_net_ipv4_conf_all_send_redirects | PASS |
| sysctl_net_ipv4_ip_forward | PASS |
| service_nftables_enabled | PASS |
| file_groupowner_backup_etc_gshadow | PASS |
| kernel_module_hfs_disabled | FAIL |
| mount_option_dev_shm_nodev | PASS |
| mount_option_dev_shm_noexec | PASS |
| mount_option_dev_shm_nosuid | PASS |
| disable_users_coredumps | PASS |
| sysctl_fs_suid_dumpable | PASS |
| sysctl_kernel_randomize_va_space | PASS |
| file_groupowner_cron_allow | unresolved script error |
| file_owner_cron_allow | unresolved script error |
| file_permissions_cron_allow | unresolved script error |
| file_permissions_cron_d | PASS |
| file_permissions_cron_daily | PASS |
| file_permissions_crontab | PASS |
| package_nis_removed | PASS |
| package_vsftpd_removed | PASS |
| package_openldap-clients_removed | PASS |
| package_rpcbind_removed | PASS |
| package_telnet_removed | PASS |
| package_rsync_removed | PASS |
