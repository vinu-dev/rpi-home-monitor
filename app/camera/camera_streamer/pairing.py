"""
Camera pairing protocol.

Handles the certificate exchange when a camera is confirmed
by the admin in the server dashboard.

Flow:
1. Camera boots unpaired → starts temporary AP for setup
2. Server generates client cert + pairing token
3. Token entered on camera setup page (via AP) or auto-exchanged
4. Camera stores client cert at /data/certs/client.crt + key
5. Camera restarts streaming with mTLS
"""

# TODO: Implement PairingManager class
