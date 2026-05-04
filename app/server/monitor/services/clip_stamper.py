# REQ: SWR-029, SWR-030; RISK: RISK-014, RISK-017; SEC: SC-014, SC-020; TEST: TC-026, TC-027
"""Best-effort clip metadata stamping for exported MP4 recordings."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitor.models import Camera, ServerMeta
from monitor.services.audit import CLIP_TIMESTAMP_REMUX_FAILED, CLIP_TIMESTAMP_REMUX_OK

log = logging.getLogger("monitor.clip_stamper")

STAMP_TIMEOUT_SECONDS = 30
CHAPTER_INTERVAL_SECONDS = 60
_FLAT_STEM_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")
_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATED_STEM_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")


def stamp_sentinel_path(clip_path: Path) -> Path:
    """Return the sentinel path tracking successful timestamp stamping."""

    return clip_path.with_suffix(f"{clip_path.suffix}.stamp.ok")


@dataclass(frozen=True)
class StampResult:
    """Outcome of a stamp attempt."""

    ok: bool
    reason: str
    elapsed_ms: int
    stamped: bool = False
    skipped: bool = False


class ClipStamper:
    """Synchronous MP4 clip stamper using ffprobe + ffmpeg."""

    def __init__(
        self,
        *,
        audit=None,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        timeout_seconds: int = STAMP_TIMEOUT_SECONDS,
        clock_state_provider=None,
    ):
        self._audit = audit
        self._ffmpeg = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self._ffprobe = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self._timeout_seconds = int(timeout_seconds)
        self._clock_state_provider = clock_state_provider
        self._warned_missing_ffmpeg = False
        self._warned_missing_ffprobe = False

    def stamp(
        self,
        clip_path: Path,
        camera: Camera | None,
        server_meta: ServerMeta,
    ) -> StampResult:
        """Stamp an MP4 clip in-place with container metadata + timestamps."""

        started = time.monotonic()
        clip_path = Path(clip_path)
        if not clip_path.is_file():
            return StampResult(False, "clip-missing", 0)

        ffmpeg = shutil.which(self._ffmpeg)
        ffprobe = shutil.which(self._ffprobe)
        if not ffprobe:
            if not self._warned_missing_ffprobe:
                log.warning(
                    "clip_stamper: ffprobe not on PATH (looked for %s)", self._ffprobe
                )
                self._warned_missing_ffprobe = True
            return self._finish(started, False, "ffprobe-missing")
        if not ffmpeg:
            if not self._warned_missing_ffmpeg:
                log.warning(
                    "clip_stamper: ffmpeg not on PATH (looked for %s)", self._ffmpeg
                )
                self._warned_missing_ffmpeg = True
            return self._finish(started, False, "ffmpeg-missing")

        sentinel = stamp_sentinel_path(clip_path)
        existing_probe = self._probe_clip(clip_path, ffprobe)
        if (
            existing_probe
            and sentinel.exists()
            and self._is_stamped_probe(existing_probe)
        ):
            return self._finish(
                started, True, "already-stamped", stamped=True, skipped=True
            )
        if existing_probe and self._is_stamped_probe(existing_probe):
            self._write_sentinel(sentinel)
            return self._finish(
                started, True, "already-stamped", stamped=True, skipped=True
            )

        timestamp = self._parse_clip_timestamp(clip_path)
        if timestamp is None:
            return self._finish(started, False, "unsupported-filename")

        duration_seconds = self._probe_duration_seconds(existing_probe)
        if duration_seconds <= 0:
            duration_seconds = 1

        camera_id = self._camera_id_for_path(clip_path, camera)
        clock_state = self._clock_state()
        title = self._build_title(camera, timestamp)
        comment = self._build_comment(server_meta, clock_state)
        make = "rpi-home-monitor"
        model = self._metadata_string(getattr(camera, "sensor_model", "") or "unknown")

        srt_path = clip_path.with_suffix(".timestamps.srt")
        ffmeta_path = clip_path.with_suffix(".chapters.ffmeta")
        stamped_path = clip_path.with_suffix(".stamped.mp4")

        try:
            srt_path.write_text(
                self._build_srt(timestamp, duration_seconds),
                encoding="utf-8",
            )
            ffmeta_path.write_text(
                self._build_ffmetadata(timestamp, duration_seconds),
                encoding="utf-8",
            )

            cmd = [
                ffmpeg,
                "-y",
                "-nostdin",
                "-i",
                str(clip_path),
                "-f",
                "srt",
                "-i",
                str(srt_path),
                "-f",
                "ffmetadata",
                "-i",
                str(ffmeta_path),
                "-map",
                "0",
                "-map",
                "1",
                "-map_metadata",
                "2",
                "-map_chapters",
                "2",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                "-disposition:s:0",
                "0",
                "-metadata",
                f"creation_time={timestamp.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
                "-metadata",
                f"title={title}",
                "-metadata",
                f"comment={comment}",
                "-metadata",
                f"make={make}",
                "-metadata",
                f"model={model}",
                "-metadata:s:s:0",
                "title=timestamps",
                "-metadata:s:s:0",
                "language=eng",
                str(stamped_path),
            ]
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=self._timeout_seconds,
            )
            if result.returncode != 0:
                self._log_audit(
                    CLIP_TIMESTAMP_REMUX_FAILED,
                    camera_id=camera_id,
                    filename=clip_path.name,
                    duration_seconds=duration_seconds,
                    clock_state=clock_state,
                    detail=self._stderr_tail(result.stderr),
                )
                self._cleanup_paths(stamped_path)
                return self._finish(started, False, "ffmpeg-failed")

            stamped_probe = self._probe_clip(stamped_path, ffprobe)
            if not stamped_probe or not self._has_video_stream(stamped_probe):
                self._log_audit(
                    CLIP_TIMESTAMP_REMUX_FAILED,
                    camera_id=camera_id,
                    filename=clip_path.name,
                    duration_seconds=duration_seconds,
                    clock_state=clock_state,
                    detail="output validation failed",
                )
                self._cleanup_paths(stamped_path)
                return self._finish(started, False, "output-invalid")

            os.replace(stamped_path, clip_path)
            self._write_sentinel(sentinel)
            self._log_audit(
                CLIP_TIMESTAMP_REMUX_OK,
                camera_id=camera_id,
                filename=clip_path.name,
                duration_seconds=duration_seconds,
                clock_state=clock_state,
                detail=f"stamped clip in {self._elapsed_ms(started)}ms",
            )
            return self._finish(started, True, "stamped", stamped=True)
        except subprocess.TimeoutExpired:
            self._log_audit(
                CLIP_TIMESTAMP_REMUX_FAILED,
                camera_id=camera_id,
                filename=clip_path.name,
                duration_seconds=duration_seconds,
                clock_state=clock_state,
                detail="ffmpeg timed out",
            )
            return self._finish(started, False, "timeout")
        except OSError as exc:
            self._log_audit(
                CLIP_TIMESTAMP_REMUX_FAILED,
                camera_id=camera_id,
                filename=clip_path.name,
                duration_seconds=duration_seconds,
                clock_state=clock_state,
                detail=str(exc),
            )
            return self._finish(started, False, "oserror")
        finally:
            self._cleanup_paths(srt_path, ffmeta_path, stamped_path)

    def probe_stamped(self, clip_path: Path) -> bool:
        """Return True iff ffprobe says the clip already carries a stamp."""

        ffprobe = shutil.which(self._ffprobe)
        if not ffprobe or not Path(clip_path).is_file():
            return False
        probe = self._probe_clip(Path(clip_path), ffprobe)
        return bool(probe and self._is_stamped_probe(probe))

    def tools_available(self) -> bool:
        return bool(shutil.which(self._ffmpeg) and shutil.which(self._ffprobe))

    def _probe_clip(self, clip_path: Path, ffprobe: str) -> dict | None:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:format_tags=creation_time,title,comment,make,model",
                    "-show_streams",
                    "-show_chapters",
                    "-of",
                    "json",
                    str(clip_path),
                ],
                check=False,
                capture_output=True,
                timeout=self._timeout_seconds,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        try:
            return json.loads(
                (result.stdout or b"{}").decode("utf-8", errors="replace")
            )
        except json.JSONDecodeError:
            return None

    def _probe_duration_seconds(self, probe: dict | None) -> int:
        if not probe:
            return 0
        try:
            value = float((probe.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            return 0
        return max(1, math.ceil(value))

    def _is_stamped_probe(self, probe: dict) -> bool:
        tags = (probe.get("format") or {}).get("tags") or {}
        title = str(tags.get("title") or "").strip()
        creation_time = str(tags.get("creation_time") or "").strip()
        if not (title and creation_time):
            return False
        for stream in probe.get("streams") or []:
            if stream.get("codec_type") == "subtitle":
                return True
        return False

    def _has_video_stream(self, probe: dict) -> bool:
        return any(
            stream.get("codec_type") == "video" for stream in probe.get("streams") or []
        )

    def _parse_clip_timestamp(self, clip_path: Path) -> datetime | None:
        stem = clip_path.stem
        match = _FLAT_STEM_RE.match(stem)
        if match:
            year, month, day, hour, minute, second = (int(v) for v in match.groups())
            return self._datetime_or_none(year, month, day, hour, minute, second)
        parent = clip_path.parent.name
        if _DATED_DIR_RE.match(parent):
            match = _DATED_STEM_RE.match(stem)
            if match:
                hour, minute, second = (int(v) for v in match.groups())
                year, month, day = (int(v) for v in parent.split("-"))
                return self._datetime_or_none(year, month, day, hour, minute, second)
        return None

    def _datetime_or_none(
        self, year: int, month: int, day: int, hour: int, minute: int, second: int
    ) -> datetime | None:
        try:
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except ValueError:
            return None

    def _build_srt(self, started_at: datetime, duration_seconds: int) -> str:
        lines: list[str] = []
        for index in range(duration_seconds):
            cue_start = self._format_srt_time(index)
            cue_end = self._format_srt_time(index + 1)
            label = (started_at + timedelta(seconds=index)).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            lines.extend([str(index + 1), f"{cue_start} --> {cue_end}", label, ""])
        return "\n".join(lines).rstrip() + "\n"

    def _build_ffmetadata(self, started_at: datetime, duration_seconds: int) -> str:
        lines = [";FFMETADATA1"]
        for start in range(0, duration_seconds, CHAPTER_INTERVAL_SECONDS):
            end = min(duration_seconds, start + CHAPTER_INTERVAL_SECONDS)
            label = (started_at + timedelta(seconds=start)).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            lines.extend(
                [
                    "[CHAPTER]",
                    "TIMEBASE=1/1",
                    f"START={start}",
                    f"END={end}",
                    f"title={label}",
                ]
            )
        return "\n".join(lines) + "\n"

    def _build_title(self, camera: Camera | None, timestamp: datetime) -> str:
        camera_name = (
            getattr(camera, "name", "") or getattr(camera, "id", "") or "camera"
        )
        return self._metadata_string(
            f"{camera_name} - {timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    def _build_comment(self, server_meta: ServerMeta, clock_state: str) -> str:
        comment = (
            f"rpi-home-monitor v{server_meta.server_version or 'unknown'} - "
            f"{server_meta.hostname or 'home-monitor'} - clock state: {clock_state}"
        )
        if clock_state == "red":
            comment += " - timestamps may be inaccurate"
        return self._metadata_string(comment)

    def _clock_state(self) -> str:
        if not callable(self._clock_state_provider):
            return "unknown"
        try:
            data = self._clock_state_provider() or {}
        except Exception:
            return "unknown"
        state = str(data.get("clock_state") or "").strip().lower()
        if state in {"green", "amber", "red", "unknown"}:
            return state
        if data.get("ntp_synchronized") is True:
            return "green"
        if data.get("ntp_synchronized") is False:
            return "red"
        return "unknown"

    def _camera_id_for_path(self, clip_path: Path, camera: Camera | None) -> str:
        if camera is not None and getattr(camera, "id", ""):
            return str(camera.id)
        parent = clip_path.parent
        if _DATED_DIR_RE.match(parent.name):
            parent = parent.parent
        return parent.name

    def _metadata_string(self, value: str) -> str:
        cleaned = (
            str(value or "").replace("\x00", "").replace("\n", "_").replace("\r", "_")
        )
        cleaned = cleaned.replace("=", "_").strip()
        return cleaned[:200]

    def _stderr_tail(self, stderr: bytes | str | None) -> str:
        if isinstance(stderr, bytes):
            text = stderr.decode("utf-8", errors="replace")
        else:
            text = str(stderr or "")
        return text.strip()[-200:] or "ffmpeg returned non-zero"

    def _format_srt_time(self, seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},000"

    def _write_sentinel(self, path: Path) -> None:
        try:
            path.write_text("ok\n", encoding="utf-8")
        except OSError:
            log.debug("clip_stamper: failed to write sentinel %s", path)

    def _cleanup_paths(self, *paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def _log_audit(self, event: str, **fields) -> None:
        if self._audit is None:
            return
        detail = fields.pop("detail", "")
        try:
            self._audit.log_event(event, detail=self._audit_detail(fields, detail))
        except Exception:
            log.debug("clip_stamper: audit log failed for %s", event)

    def _audit_detail(self, fields: dict, detail: str) -> str:
        parts = [
            f"{key}={value}" for key, value in fields.items() if value not in ("", None)
        ]
        if detail:
            parts.append(detail)
        return " ".join(parts)

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _finish(
        self,
        started: float,
        ok: bool,
        reason: str,
        *,
        stamped: bool = False,
        skipped: bool = False,
    ) -> StampResult:
        return StampResult(
            ok=ok,
            reason=reason,
            elapsed_ms=self._elapsed_ms(started),
            stamped=stamped,
            skipped=skipped,
        )
