# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# Apply Adiantum encryption kernel config fragment (ADR-0010)
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += "file://adiantum.cfg"
