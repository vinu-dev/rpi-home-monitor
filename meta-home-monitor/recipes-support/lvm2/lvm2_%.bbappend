# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# lvm2 installs several plugin/pkgconfig paths in parallel; serialize
# do_install to avoid races while creating shared destination directories.
PARALLEL_MAKEINST = ""
