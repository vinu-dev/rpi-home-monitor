# Enable libcamera Python bindings (libcamera-pycamera package).
#
# The upstream meta-openembedded libcamera recipe (meta-multimedia) gates
# the Python bindings behind PACKAGECONFIG[pycamera] and ships them
# disabled by default. Our camera-streamer Python app uses Picamera2
# which *requires* those bindings — without this append the
# `import libcamera` inside picamera2 fails at start-up.
#
# See docs/exec-plans/motion-detection.md §Phase-2 for the design.
PACKAGECONFIG:append = " pycamera"
