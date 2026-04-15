# Enable nginx to start automatically on boot
SYSTEMD_AUTO_ENABLE = "enable"

# Enable auth_request module for session-validated video serving.
# nginx auth_request makes a subrequest to Flask /auth/check before
# serving video content (/live/, /clips/, /webrtc/, /snapshots/).
PACKAGECONFIG:append = " http-auth-request"

# Remove the default server config that conflicts with our monitor.conf
# (default_server listens on port 80, conflicts with our HTTP→HTTPS redirect)
do_install:append() {
    rm -f ${D}${sysconfdir}/nginx/sites-enabled/default_server
    rm -f ${D}${sysconfdir}/nginx/sites-available/default_server
}
