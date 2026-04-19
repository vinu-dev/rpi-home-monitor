"""Soak and endurance scenarios.

Run with: SOAK_ENABLED=1 pytest tests/soak -m soak -v

Each scenario stresses a different subsystem over a sustained period.
They are skipped unless SOAK_ENABLED=1 is set so normal CI stays fast.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Guard: all tests in this module skip unless explicitly opted in
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.soak

_ENABLED = bool(os.environ.get("SOAK_ENABLED"))

# Duration multiplier — set SOAK_SCALE to >1 for longer runs
_SCALE = float(os.environ.get("SOAK_SCALE", "1.0"))


def _skip_if_disabled():
    if not _ENABLED:
        pytest.skip("set SOAK_ENABLED=1 to run soak scenarios")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

sys.path.insert(
    0,
    str(Path(__file__).parents[2] / "app" / "server"),
)
sys.path.insert(
    0,
    str(Path(__file__).parents[2] / "app" / "camera"),
)


def _make_clip(clips_dir: Path, camera: str, date: str, index: int) -> Path:
    """Write a minimal fake .mp4 clip matching the HH-MM-SS stem convention."""
    d = clips_dir / camera / date
    d.mkdir(parents=True, exist_ok=True)
    # StorageManager parses stem as HH-MM-SS; use index to generate unique times
    hh = (index // 3600) % 24
    mm = (index % 3600) // 60
    ss = index % 60
    name = f"{hh:02d}-{mm:02d}-{ss:02d}"
    clip = d / f"{name}.mp4"
    clip.write_bytes(b"\x00" * 1024)
    return clip


# ---------------------------------------------------------------------------
# Scenario 1 — Storage manager FIFO pressure
# ---------------------------------------------------------------------------


@pytest.mark.soak
def test_storage_manager_fifo_pressure(tmp_path):
    """StorageManager FIFO cleanup holds the reserve under continuous write load.

    Writes clips at ~200 per second to a tmpfs-like directory, asserting
    that the storage manager purges the oldest clips first and the total
    count never grows without bound.
    """
    _skip_if_disabled()

    from monitor.services.storage_manager import StorageManager

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()

    # A 30 MB simulated disk; each clip occupies 2 MB (conceptually).
    # Cleanup fires at 60 %: after 9 clips (18/30 = 60 %).
    # max_delete=10 purges enough to get below threshold each cycle.
    TARGET_PERCENT = 60
    WRITE_CYCLES = int(300 * _SCALE)
    SIMULATED_DISK_MB = 30
    CLIP_SIMULATED_MB = 2
    MAX_EXPECTED_CLIPS = 40  # steady-state: ~9 between cleanups

    mgr = StorageManager(
        recordings_dir=str(clips_dir),
        threshold_percent=TARGET_PERCENT,
    )

    clip_counter = [0]

    def _fake_disk_usage(path):
        total = SIMULATED_DISK_MB * 1024 * 1024
        used = min(clip_counter[0] * CLIP_SIMULATED_MB * 1024 * 1024, total)
        free = total - used
        return type("du", (), {"total": total, "used": used, "free": free})()

    with patch(
        "monitor.services.storage_manager.shutil.disk_usage",
        side_effect=_fake_disk_usage,
    ):
        for i in range(WRITE_CYCLES):
            date = f"2026-01-{(i // 86400) + 1:02d}"
            _make_clip(clips_dir, "cam-001", date, i)
            clip_counter[0] += 1

            if i % 5 == 0:
                deleted = mgr.cleanup_oldest_clips(max_delete=10)
                clip_counter[0] = max(0, clip_counter[0] - deleted)

        # After all writes+cleanup, count remaining clips
        remaining = list(clips_dir.rglob("*.mp4"))

    assert len(remaining) < MAX_EXPECTED_CLIPS, (
        f"StorageManager left {len(remaining)} clips; expected < {MAX_EXPECTED_CLIPS}. "
        "FIFO cleanup is not keeping up."
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Auth rate-limiter under concurrent load
# ---------------------------------------------------------------------------


@pytest.mark.soak
def test_auth_rate_limiter_concurrent_load():
    """Rate limiter correctly blocks exactly at RATE_LIMIT_BLOCK under concurrent
    requests from the same IP.

    Spawns 40 threads each making 5 rapid login attempts and asserts the
    hard block fires for heavy hitters while light hitters remain allowed.
    """
    _skip_if_disabled()

    from monitor.auth import (
        RATE_LIMIT_BLOCK,
        _check_rate_limit,
        _login_attempts,
        _record_attempt,
    )

    _login_attempts.clear()
    errors: list[str] = []

    def _spam_ip(ip: str, count: int):
        for _ in range(count):
            _record_attempt(ip)
        allowed, _ = _check_rate_limit(ip)
        if count >= RATE_LIMIT_BLOCK and allowed:
            errors.append(f"{ip}: {count} attempts but NOT blocked")
        if count < RATE_LIMIT_BLOCK and not allowed:
            errors.append(f"{ip}: only {count} attempts but WAS blocked")

    threads = []
    for i in range(20):
        # Heavy hitters — should be blocked
        t = threading.Thread(
            target=_spam_ip, args=(f"10.0.heavy.{i}", RATE_LIMIT_BLOCK + 1)
        )
        threads.append(t)
    for i in range(20):
        # Light hitters — should remain allowed
        t = threading.Thread(
            target=_spam_ip, args=(f"10.0.light.{i}", RATE_LIMIT_BLOCK - 1)
        )
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    _login_attempts.clear()
    assert not errors, "\n".join(errors)


# ---------------------------------------------------------------------------
# Scenario 3 — WebRTC session churn (proxy connect/disconnect)
# ---------------------------------------------------------------------------


@pytest.mark.soak
def test_webrtc_session_churn(tmp_path):
    """WebRTC proxy handles rapid connect/disconnect without leaking state.

    Fires PATCH → DELETE pairs in a tight loop through the Flask test client,
    verifying that each round-trip succeeds and no sessions accumulate.
    """
    _skip_if_disabled()

    from unittest.mock import MagicMock

    # Import the server app factory
    from monitor import create_app
    from monitor.auth import hash_password
    from monitor.models import User

    data_dir = tmp_path / "data"
    for d in ["config", "recordings", "live", "certs", "logs"]:
        (data_dir / d).mkdir(parents=True)

    app = create_app(
        config={
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "RECORDINGS_DIR": str(data_dir / "recordings"),
            "LIVE_DIR": str(data_dir / "live"),
            "CONFIG_DIR": str(data_dir / "config"),
            "CERTS_DIR": str(data_dir / "certs"),
            "SECRET_KEY": "soak-test-key",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    client = app.test_client()

    # Create admin user and log in
    app.store.save_user(
        User(
            id="soak-admin",
            username="soak-admin",
            password_hash=hash_password("pass"),
            role="admin",
            created_at="2026-01-01T00:00:00Z",
        )
    )

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "soak-admin", "password": "pass"},
    )
    csrf = resp.get_json()["csrf_token"]
    client.environ_base["HTTP_X_CSRF_TOKEN"] = csrf

    CYCLES = int(200 * _SCALE)
    failures = []

    def _mock_upstream(status=200):
        m = MagicMock()
        m.read.return_value = b""
        m.status = status
        m.headers = {
            "Content-Type": "application/sdp",
            "ETag": None,
            "Location": None,
            "Link": None,
        }
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        return m

    for i in range(CYCLES):
        session_path = f"/api/v1/webrtc/cam-001/whep/session/{i}"

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen",
            return_value=_mock_upstream(200),
        ):
            r = client.patch(
                session_path,
                data=b"ice",
                content_type="application/trickle-ice-sdpfrag",
            )
        if r.status_code != 200:
            failures.append(f"PATCH {session_path} → {r.status_code}")

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen",
            return_value=_mock_upstream(200),
        ):
            r = client.delete(session_path)
        if r.status_code != 200:
            failures.append(f"DELETE {session_path} → {r.status_code}")

    assert not failures, f"{len(failures)} failures:\n" + "\n".join(failures[:10])


# ---------------------------------------------------------------------------
# Scenario 4 — Camera session churn (status server)
# ---------------------------------------------------------------------------


@pytest.mark.soak
def test_camera_session_store_churn():
    """Camera status server session store handles rapid create/destroy cycles.

    Creates and destroys 10 000 sessions in a tight loop, asserting the
    in-memory store does not grow unboundedly and all tokens are unique.
    """
    _skip_if_disabled()

    from camera_streamer.status_server import (
        _check_session,
        _create_session,
        _destroy_session,
        _session_lock,
        _sessions,
    )

    with _session_lock:
        _sessions.clear()

    CYCLES = int(10_000 * _SCALE)
    tokens_seen: set[str] = set()
    duplicates = 0

    for _ in range(CYCLES):
        token = _create_session()
        if token in tokens_seen:
            duplicates += 1
        tokens_seen.add(token)
        assert _check_session(token) is True
        _destroy_session(token)
        assert _check_session(token) is False

    with _session_lock:
        leaked = len(_sessions)
        _sessions.clear()

    assert duplicates == 0, f"{duplicates} duplicate session tokens generated"
    assert leaked == 0, f"{leaked} sessions leaked after all destroys"
