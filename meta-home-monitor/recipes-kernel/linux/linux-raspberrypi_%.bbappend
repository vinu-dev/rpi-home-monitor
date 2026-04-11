# Apply Adiantum encryption kernel config fragment (ADR-0010)
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += "file://adiantum.cfg"
