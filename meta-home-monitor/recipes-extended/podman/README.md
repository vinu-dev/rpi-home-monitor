# Home Monitor add-on container stacks

This recipe ships systemd machinery for running optional **podman compose**
stacks (Claude automation, RADIUS, generic user containers, etc.) on the
RPi 4B server **without** modifying the core image.

It is server-only — the camera build (RPi Zero 2W, 176 MB usable RAM)
does not pull in podman or these units.

## Files

| Path on target | Purpose |
| --- | --- |
| `/usr/lib/systemd/system/hm-stacks@.service` | Template unit. One instance per stack. |
| `/usr/lib/systemd/system/hm-stacks-restore.service` | One-shot, runs each boot, re-enables stacks listed under `/data`. |
| `/usr/libexec/hm-stacks/restore.sh` | Helper invoked by the restore unit. |
| `/etc/containers/storage.conf` | Pins podman storage to `/data/containers/storage`. |

## Convention

`/data` is on its own ext4 partition (`mmcblk0p4`) and survives both
reboots and OTA rootfs A/B slot swaps. The user's stack definitions
live there:

    /data/stacks/<name>/docker-compose.yml      # the stack
    /data/stacks/<name>/enabled                 # presence = "user wants this on"

## Activating a stack

    # 1. Drop the compose file and mark it enabled
    mkdir -p /data/stacks/myapp
    cat > /data/stacks/myapp/docker-compose.yml <<EOF
    version: "3.9"
    services:
      hello:
        image: docker.io/library/hello-world
    EOF
    touch /data/stacks/myapp/enabled

    # 2. Enable the per-boot restorer (only needs to be done once,
    #    but is idempotent and safe to re-run)
    systemctl enable --now hm-stacks-restore.service

    # 3. Start it now (or wait for the next boot)
    systemctl enable --now hm-stacks@myapp.service

## Why the restorer exists

OTAs (`swupdate`) flip the active rootfs partition. The new partition
has a clean `/etc/systemd/system/` with no enable-symlinks. Without the
restorer, every OTA would silently disable every stack until the user
manually re-enables them. Reading `/data/stacks/*/enabled` on each boot
sidesteps that — the source of truth is on the persistent partition.

## Disabling a stack

    rm /data/stacks/<name>/enabled
    systemctl disable --now hm-stacks@<name>.service

(Removing only the symlink without the marker file would let the
restorer re-enable it on next boot, which is intentional.)

## Storage layout

Pulled images, volumes, and overlay diffs all live under
`/data/containers/`. This ensures:

1. They persist across OTA rootfs swaps.
2. The rootfs (read-mostly, ~700 MB) doesn't bloat with downloaded images.
3. `/data` runs out of space gracefully without bricking the system —
   `df /data` warns; `df /` stays clean.

## Defaults

Neither systemd unit is enabled out of the box. The image ships podman
ready to use but no stacks active. This keeps idle RAM/CPU near zero for
users who never opt in.
