# lvm2 installs several plugin/pkgconfig paths in parallel; serialize
# do_install to avoid races while creating shared destination directories.
PARALLEL_MAKEINST = ""
