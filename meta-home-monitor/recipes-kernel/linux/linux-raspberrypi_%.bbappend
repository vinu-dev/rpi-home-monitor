# Apply Adiantum encryption kernel config fragment (ADR-0010)
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += "file://adiantum.cfg"

# Add Docker/Podman kernel options on the Pi 4B server only.
# raspberrypi4-64 is the server MACHINE; home-monitor-camera (Zero 2W)
# stays untouched and does not pull this fragment.
SRC_URI:append:raspberrypi4-64 = " file://docker.cfg"
