# ADR-0001: Custom Yocto Distribution Instead of Poky

## Status
Accepted

## Context
We need a Linux distribution for Raspberry Pi devices (server + camera nodes). Options:
1. Use Poky (Yocto reference distro) with local.conf overrides
2. Use Raspberry Pi OS and manage packages manually
3. Create a custom Yocto distribution (`home-monitor`)

## Decision
Create a custom distribution `home-monitor` in `meta-home-monitor/conf/distro/home-monitor.conf`.

## Rationale
- **Reproducible builds**: Every image bit-for-bit identical from source
- **Security**: Minimal attack surface — only needed packages included
- **Policy centralization**: All distro features (systemd, usrmerge, WiFi, PAM) in one file, not scattered across local.conf
- **Version pinning**: Kernel 6.6, Python 3.12, OpenSSL 3.5 locked at distro level
- **Dev/Prod variants**: Same base, different security posture via image recipes

## Consequences
- Higher initial setup cost (Yocto learning curve, build infrastructure)
- Long build times (~2-4 hours for first build)
- Contributors need Yocto knowledge for OS-level changes
- App-level changes don't require rebuilds (rsync deploy)
