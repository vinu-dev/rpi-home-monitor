# REQ: SWR-064; RISK: RISK-021; SEC: SC-021; TEST: TC-042
"""Contract tests for the watchdog liveness endpoint."""


def test_healthz_requires_no_auth(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.data == b"ok\n"
    assert response.headers["Content-Type"] == "text/plain"


def test_healthz_rejects_non_loopback(client):
    response = client.get("/healthz", environ_base={"REMOTE_ADDR": "203.0.113.10"})

    assert response.status_code == 403
    assert response.data == b"forbidden\n"
    assert response.headers["Content-Type"] == "text/plain"
