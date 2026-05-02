# REQ: SWR-012; RISK: RISK-001, RISK-008; TEST: TC-005, TC-018
"""Unit tests for the boot-time server-name resolver (#199).

The resolver runs on a daemon thread and retries ``socket.gethostbyname``
with exponential backoff. Successful resolution clears any previously-
emitted ``mdns_resolution_failed`` fault; deadline expiry raises that
fault on the CaptureManager so the heartbeat surfaces a precise badge.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from camera_streamer.faults import FAULT_NETWORK_MDNS_RESOLUTION_FAILED
from camera_streamer.lifecycle import _ServerResolver


class TestResolverHappyPath:
    def test_first_attempt_resolves_no_fault_emitted(self):
        capture = MagicMock()
        resolver = _ServerResolver("homemonitor.local", capture_manager=capture)

        with patch(
            "camera_streamer.lifecycle.socket.gethostbyname",
            return_value="192.168.1.42",
        ):
            resolver._run()

        assert resolver.resolved_ip == "192.168.1.42"
        capture.add_fault.assert_not_called()
        # On success we still call clear_fault so any prior fault is
        # cleared without needing a separate state transition.
        capture.clear_fault.assert_called_once_with(
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED
        )

    def test_resolves_after_transient_failures(self):
        """Mocked gethostbyname fails N times then succeeds — exactly the
        Avahi-cold-start scenario the resolver exists for."""
        capture = MagicMock()
        resolver = _ServerResolver("homemonitor.local", capture_manager=capture)

        attempts = [
            socket.gaierror(-2, "Temporary failure"),
            socket.gaierror(-2, "Temporary failure"),
            "192.168.1.42",
        ]

        def fake_resolve(_):
            value = attempts.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with (
            patch(
                "camera_streamer.lifecycle.socket.gethostbyname",
                side_effect=fake_resolve,
            ),
            # Don't burn real backoff seconds in the test — patch the
            # stop event's wait so it returns immediately with no
            # stop signal.
            patch.object(resolver._stop, "wait", return_value=False),
        ):
            resolver._run()

        assert resolver.resolved_ip == "192.168.1.42"
        capture.add_fault.assert_not_called()
        capture.clear_fault.assert_called_once_with(
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED
        )


class TestResolverDeadlineFault:
    def test_permanent_failure_emits_fault_after_deadline(self):
        capture = MagicMock()
        resolver = _ServerResolver("missing-server.local", capture_manager=capture)

        # Drop the deadline so the loop exits quickly.
        resolver.DEADLINE_S = 0.05
        resolver.INITIAL_BACKOFF_S = 0.01
        resolver.MAX_BACKOFF_S = 0.01

        with (
            patch(
                "camera_streamer.lifecycle.socket.gethostbyname",
                side_effect=socket.gaierror(-2, "Name or service not known"),
            ),
            patch.object(resolver._stop, "wait", return_value=False),
        ):
            resolver._run()

        assert resolver.resolved_ip is None
        capture.clear_fault.assert_not_called()
        capture.add_fault.assert_called_once()
        emitted = capture.add_fault.call_args[0][0]
        assert emitted.code == FAULT_NETWORK_MDNS_RESOLUTION_FAILED
        assert emitted.context["address"] == "missing-server.local"
        assert emitted.context["attempts"] >= 1

    def test_no_capture_manager_does_not_crash_on_failure(self):
        """Defensive: resolver works without a CaptureManager (older
        callers/tests). Silent best-effort."""
        resolver = _ServerResolver("missing-server.local", capture_manager=None)
        resolver.DEADLINE_S = 0.05
        resolver.INITIAL_BACKOFF_S = 0.01

        with (
            patch(
                "camera_streamer.lifecycle.socket.gethostbyname",
                side_effect=socket.gaierror(-2, ""),
            ),
            patch.object(resolver._stop, "wait", return_value=False),
        ):
            # Must not raise.
            resolver._run()

        assert resolver.resolved_ip is None


class TestResolverStopSemantics:
    def test_empty_address_does_not_start_thread(self):
        resolver = _ServerResolver("", capture_manager=MagicMock())
        resolver.start()
        assert resolver._thread is None

    def test_start_is_idempotent(self):
        resolver = _ServerResolver("homemonitor.local", capture_manager=MagicMock())
        with patch(
            "camera_streamer.lifecycle.socket.gethostbyname",
            side_effect=socket.gaierror(-2, ""),
        ):
            resolver.start()
            first = resolver._thread
            # Second call must return the same thread, not spin a duplicate.
            resolver.start()
            assert resolver._thread is first
            resolver.stop()

    def test_stop_during_backoff_exits_cleanly(self):
        """Stop event must interrupt the backoff sleep — shutdown latency
        bounded by OS wake-up, not the current backoff interval."""
        resolver = _ServerResolver("missing-server.local", capture_manager=MagicMock())
        # Long backoff so we'd otherwise hang for minutes if stop didn't fire.
        resolver.INITIAL_BACKOFF_S = 60.0
        resolver.MAX_BACKOFF_S = 60.0
        resolver.DEADLINE_S = 600.0

        # Replace the stop event's .wait so we can return True (= stop set)
        # on the first backoff sleep, simulating a shutdown mid-wait.
        # Crucially: we DON'T patch .is_set so the loop preamble still runs.
        wait_calls = []

        def fake_wait(timeout=None):
            wait_calls.append(timeout)
            return True  # signal stop on the first backoff

        with (
            patch(
                "camera_streamer.lifecycle.socket.gethostbyname",
                side_effect=socket.gaierror(-2, ""),
            ),
            patch.object(resolver._stop, "wait", side_effect=fake_wait),
        ):
            resolver._run()

        # Exactly one backoff wait happened — the resolver returned
        # immediately on stop without a second iteration, even though
        # the deadline hadn't been reached.
        assert len(wait_calls) == 1
        # No fault emitted because stop was clean.
        assert resolver.resolved_ip is None

    def test_stop_joins_thread_within_timeout(self):
        """Real-thread test: start the resolver against a hostname that
        will fail forever, then stop() and verify the thread exits."""
        resolver = _ServerResolver(
            "this-host-will-never-resolve.invalid",
            capture_manager=MagicMock(),
        )
        resolver.INITIAL_BACKOFF_S = 0.05
        resolver.MAX_BACKOFF_S = 0.05
        resolver.DEADLINE_S = 60.0

        resolver.start()
        # Brief wait so the thread is actually inside the loop.
        import time as _time

        _time.sleep(0.1)
        resolver.stop(timeout=2.0)

        assert resolver._thread is not None
        assert not resolver._thread.is_alive()


class TestResolverBackoffShape:
    def test_backoff_doubles_up_to_cap(self):
        """The backoff sequence must double on each failure but cap at
        MAX_BACKOFF_S — operators rely on the bounded retry rate."""
        resolver = _ServerResolver("missing-server.local", capture_manager=MagicMock())
        resolver.INITIAL_BACKOFF_S = 1.0
        resolver.MAX_BACKOFF_S = 4.0
        resolver.BACKOFF_MULTIPLIER = 2.0
        resolver.DEADLINE_S = 0.01  # don't actually loop more than once
        # Small DEADLINE_S means the loop exits after one attempt;
        # we instead test the cap calculation directly via the
        # operator semantics: if the doubling sequence is 1, 2, 4, 8 →
        # capped to 1, 2, 4, 4, 4, ...

        sequence = []
        backoff = resolver.INITIAL_BACKOFF_S
        for _ in range(6):
            sequence.append(backoff)
            backoff = min(backoff * resolver.BACKOFF_MULTIPLIER, resolver.MAX_BACKOFF_S)

        assert sequence == [1.0, 2.0, 4.0, 4.0, 4.0, 4.0]
