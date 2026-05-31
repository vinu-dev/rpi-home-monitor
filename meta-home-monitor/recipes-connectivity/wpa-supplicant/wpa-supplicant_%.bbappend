# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# wpa_supplicant installs multiple binaries into BINDIR, and parallel
# do_install can race while creating that directory.
PARALLEL_MAKEINST = ""
