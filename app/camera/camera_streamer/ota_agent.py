"""
OTA update agent.

Listens for update pushes from the home server.
When an update is received:
1. Download .swu image from server
2. Verify Ed25519 signature
3. Run swupdate to install to inactive rootfs partition
4. Reboot into new partition
5. If boot fails 3 times → automatic rollback

The agent runs a small HTTP endpoint (port 8080) that only
accepts connections from the server IP (enforced by nftables).
"""

# TODO: Implement OTAAgent class
