# =============================================================
# base-files customization
# =============================================================
# OS branding is handled by os-release.bbappend — do not add
# /etc/os-release here as it conflicts with the os-release package.
#
# NOTE: /data partition fstab entry is generated automatically by
# wic from the "part /data" line in the .wks layout file.
# Do NOT add a duplicate entry here — it causes systemd-fstab-generator
# to fail with "Duplicate entry in /etc/fstab".
