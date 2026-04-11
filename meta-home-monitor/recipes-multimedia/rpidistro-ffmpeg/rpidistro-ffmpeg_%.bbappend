# Enable OpenSSL/TLS support for mTLS RTSP streaming (ADR-0009)
# rpidistro-ffmpeg ships with --disable-openssl by default.
# Camera needs RTSPS (TLS-wrapped RTSP) for mutual TLS authentication.
PACKAGECONFIG:append = " openssl"
