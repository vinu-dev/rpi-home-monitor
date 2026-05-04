# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
SUMMARY = "Web server packages for Home Monitor"
DESCRIPTION = "Nginx, Flask, and Python dependencies for the web dashboard."
LICENSE = "MIT"

inherit packagegroup

RDEPENDS:${PN} = " \
    nginx \
    python3 \
    python3-flask \
    python3-jinja2 \
    python3-requests \
    python3-bcrypt \
    python3-pyotp \
    python3-pip \
    "
