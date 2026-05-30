# wpa_supplicant installs multiple binaries into BINDIR, and parallel
# do_install can race while creating that directory.
PARALLEL_MAKEINST = ""
