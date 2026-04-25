#!/bin/sh
# hm-stacks restore — runs once per boot from hm-stacks-restore.service.
#
# OTAs land us on a fresh rootfs whose /etc/systemd/system/ has no
# enable symlinks for any stacks the user previously opted into.
# /data is persistent across A/B slots, so the source of truth for
# "is this stack enabled?" is the marker file:
#
#     /data/stacks/<name>/enabled
#
# For each stack with that marker, ensure systemd has the
# hm-stacks@<name>.service enable symlink. systemd-analyze verify
# could be added later but is intentionally omitted here so a single
# bad compose file doesn't block other stacks.

set -eu

STACKS_DIR=/data/stacks

if [ ! -d "$STACKS_DIR" ]; then
    exit 0
fi

restored=0
for marker in "$STACKS_DIR"/*/enabled; do
    [ -e "$marker" ] || continue
    stack_dir=$(dirname "$marker")
    stack=$(basename "$stack_dir")

    # Sanity: must have a compose file.
    if [ ! -f "$stack_dir/docker-compose.yml" ]; then
        echo "hm-stacks: skipping '$stack' — no docker-compose.yml" >&2
        continue
    fi

    unit="hm-stacks@${stack}.service"
    # Idempotent — `enable` is a no-op if already enabled.
    if systemctl enable "$unit" >/dev/null 2>&1; then
        echo "hm-stacks: enabled $unit"
        restored=$((restored + 1))
    else
        echo "hm-stacks: failed to enable $unit (continuing)" >&2
    fi
done

echo "hm-stacks: restored $restored stack(s)"
exit 0
