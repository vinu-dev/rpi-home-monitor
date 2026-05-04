# REQ: SWR-058, SWR-059, SWR-060, SWR-061; RISK: RISK-023, RISK-024, RISK-025; SEC: SC-022, SC-023, SC-024; TEST: TC-050, TC-051, TC-052, TC-053
"""Share-link service.

Admins can mint revocable, time-limited public links for exactly one clip or
one live camera. Public recipients never get a session; every request is
validated against the stored token + scope.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitor.models import ShareLink

log = logging.getLogger("monitor.share-link-service")

_CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TTL_SECONDS = {
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "never": None,
}
_ALLOWED_PUBLIC_LIVE_SUFFIXES = (".m3u8", ".ts", ".jpg")
_PUBLIC_LINK_FAILURE = (
    "This link is no longer available. Contact the person who shared it."
)
_PUBLIC_RESOURCE_FAILURE = (
    "This shared resource is not available right now. Contact the person who shared it."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _redact_token(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:10]}...{token[-6:]}"


class ShareLinkService:
    """Business logic for admin-issued public share links."""

    def __init__(self, store, recordings_service, live_dir: str, audit=None):
        self._store = store
        self._recordings_service = recordings_service
        self._live_dir = Path(live_dir)
        self._audit = audit

    # ------------------------------------------------------------------
    # Public helpers used by routes and templates
    # ------------------------------------------------------------------

    @staticmethod
    def build_clip_resource_id(camera_id: str, clip_date: str, filename: str) -> str:
        return f"{camera_id}/{clip_date}/{filename}"

    @staticmethod
    def clip_resource_parts(resource_id: str) -> tuple[str, str, str] | None:
        parts = (resource_id or "").split("/")
        if len(parts) != 3:
            return None
        camera_id, clip_date, filename = parts
        if not _CAMERA_ID_RE.match(camera_id):
            return None
        if not _DATE_RE.match(clip_date):
            return None
        if not filename.endswith(".mp4"):
            return None
        return camera_id, clip_date, filename

    @staticmethod
    def public_link_failure_message() -> str:
        return _PUBLIC_LINK_FAILURE

    @staticmethod
    def public_resource_failure_message() -> str:
        return _PUBLIC_RESOURCE_FAILURE

    # ------------------------------------------------------------------
    # Admin operations
    # ------------------------------------------------------------------

    def create_share_link(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        owner_username: str,
        ttl: str,
        pin_ip: bool = False,
        pin_ua: bool = False,
        note: str = "",
        requesting_ip: str = "",
        base_url: str = "",
    ):
        """Mint and persist a share link for exactly one resource."""
        resource_meta, error, status = self._validate_resource_reference(
            resource_type, resource_id, require_available=True
        )
        if error:
            return None, error, status

        if ttl not in _TTL_SECONDS:
            return None, "Invalid TTL", 400
        if not owner_id or not owner_username:
            return None, "Owner identity required", 400

        token = "sharelink_" + secrets.token_urlsafe(24)
        created_at = _utc_now()
        ttl_seconds = _TTL_SECONDS[ttl]
        expires_at = ""
        if ttl_seconds is not None:
            expires_at = (created_at + timedelta(seconds=ttl_seconds)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        share_link = ShareLink(
            token=token,
            resource_type=resource_type,
            resource_id=resource_id,
            owner_id=owner_id,
            owner_username=owner_username,
            created_at=created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=expires_at,
            note=(note or "").strip(),
            pin_ip=bool(pin_ip),
            pin_ua=bool(pin_ua),
        )
        self._store.save_share_link(share_link)

        pin_summary = []
        if share_link.pin_ip:
            pin_summary.append("ip")
        if share_link.pin_ua:
            pin_summary.append("ua")
        pin_label = ",".join(pin_summary) if pin_summary else "none"
        self._log_audit(
            "SHARE_LINK_CREATED",
            user=owner_username,
            ip=requesting_ip,
            detail=(
                f"token={_redact_token(token)} resource={resource_type}:{resource_id} "
                f"ttl={ttl} pin={pin_label} note={share_link.note or '-'}"
            ),
        )

        return (
            self.serialize_share_link(
                share_link,
                base_url=base_url,
                resource_name=resource_meta["resource_name"],
            ),
            None,
            201,
        )

    def list_share_links(
        self, resource_type: str, resource_id: str, base_url: str = ""
    ):
        """List active share links for one resource."""
        resource_meta, error, status = self._validate_resource_reference(
            resource_type, resource_id, require_available=False
        )
        if error:
            return None, error, status

        links = [
            link
            for link in self._store.get_share_links()
            if link.resource_type == resource_type
            and link.resource_id == resource_id
            and not link.revoked_at
            and not self._is_expired(link)
        ]
        links.sort(key=lambda link: link.created_at or "", reverse=True)
        return (
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "resource_name": resource_meta["resource_name"],
                "links": [
                    self.serialize_share_link(
                        link,
                        base_url=base_url,
                        resource_name=resource_meta["resource_name"],
                    )
                    for link in links
                ],
            },
            None,
            200,
        )

    def revoke_share_link(
        self, token: str, requesting_user: str = "", requesting_ip: str = ""
    ):
        """Revoke a share link immediately."""
        link = self._store.get_share_link(token)
        if link is None:
            return None, "Share link not found", 404

        if not link.revoked_at:
            link.revoked_at = _utc_now_iso()
            self._store.save_share_link(link)
            self._log_audit(
                "SHARE_LINK_REVOKED",
                user=requesting_user,
                ip=requesting_ip,
                detail=(
                    f"token={_redact_token(link.token)} "
                    f"resource={link.resource_type}:{link.resource_id}"
                ),
            )
        return {"message": "Share link revoked"}, None, 200

    # ------------------------------------------------------------------
    # Public access operations
    # ------------------------------------------------------------------

    def check_public_rate_limit(self, visitor_ip: str) -> tuple[bool, bool]:
        """Reuse the shared login/public-reader IP bucket."""
        from monitor.auth import _check_rate_limit

        return _check_rate_limit(visitor_ip or "")

    def record_failed_public_attempt(
        self, visitor_ip: str, token: str, reason: str
    ) -> None:
        """Record a failed public request in the shared IP bucket + audit log."""
        from monitor.auth import _record_attempt

        if visitor_ip:
            _record_attempt(visitor_ip)
        self._log_audit(
            "SHARE_LINK_REJECTED",
            user="public",
            ip=visitor_ip,
            detail=f"token={_redact_token(token)} reason={reason}",
        )

    def get_shared_clip_page(self, token: str, visitor_ip: str, visitor_ua: str):
        """Authorise and describe a public clip viewer page."""
        link, error, status = self._authorise_link(
            token, expected_type="clip", visitor_ip=visitor_ip, visitor_ua=visitor_ua
        )
        if error:
            return None, error, status

        clip_result, error, status = self._resolve_clip_resource(link.resource_id)
        if error:
            return None, error, status

        self._record_access(link, visitor_ip)
        return (
            {
                "share_link": link,
                "resource_name": clip_result["resource_name"],
                "video_url": f"/share/clip/{link.token}/video.mp4",
                "device_name": self._device_name(),
            },
            None,
            200,
        )

    def get_shared_clip_asset(self, token: str, visitor_ip: str, visitor_ua: str):
        """Authorise and resolve the clip file behind a public token."""
        link, error, status = self._authorise_link(
            token, expected_type="clip", visitor_ip=visitor_ip, visitor_ua=visitor_ua
        )
        if error:
            return None, error, status
        return self._resolve_clip_resource(link.resource_id)

    def get_shared_camera_page(self, token: str, visitor_ip: str, visitor_ua: str):
        """Authorise and describe a public camera viewer page."""
        link, error, status = self._authorise_link(
            token, expected_type="camera", visitor_ip=visitor_ip, visitor_ua=visitor_ua
        )
        if error:
            return None, error, status

        result, error, status = self._resolve_camera_resource(link.resource_id)
        if error:
            return None, error, status

        self._record_access(link, visitor_ip)
        return (
            {
                "share_link": link,
                "resource_name": result["resource_name"],
                "camera_id": result["camera_id"],
                "hls_url": f"/share/camera/{link.token}/stream.m3u8",
                "device_name": self._device_name(),
            },
            None,
            200,
        )

    def get_shared_camera_file(
        self,
        token: str,
        visitor_ip: str,
        visitor_ua: str,
        filename: str,
    ):
        """Authorise and resolve a public HLS playlist/segment file."""
        link, error, status = self._authorise_link(
            token, expected_type="camera", visitor_ip=visitor_ip, visitor_ua=visitor_ua
        )
        if error:
            return None, error, status

        result, error, status = self._resolve_camera_resource(link.resource_id)
        if error:
            return None, error, status

        if not filename.endswith(_ALLOWED_PUBLIC_LIVE_SUFFIXES):
            return None, "Invalid file type", 400

        live_root = self._live_dir
        try:
            path = (live_root / result["camera_id"] / filename).resolve()
            path.relative_to(live_root.resolve())
        except (OSError, ValueError):
            return None, "Invalid path", 400
        if not path.is_file():
            return None, self.public_resource_failure_message(), 404
        return (
            {
                "path": path,
                "mimetype": self._public_live_mimetype(path.suffix),
            },
            None,
            200,
        )

    # ------------------------------------------------------------------
    # Serialisation + cleanup
    # ------------------------------------------------------------------

    def serialize_share_link(
        self,
        share_link: ShareLink,
        *,
        base_url: str = "",
        resource_name: str = "",
    ) -> dict:
        """Stable API shape for admin pages/tests."""
        payload = asdict(share_link)
        payload["resource_name"] = resource_name or self._resource_name_for_link(
            share_link
        )
        payload["share_url"] = self.public_url_for_link(share_link, base_url=base_url)
        payload["status"] = self._status_for_link(share_link)
        payload["ttl_remaining_seconds"] = self._ttl_remaining_seconds(share_link)
        payload["pinned_ip_bound"] = bool(share_link.pinned_ip)
        payload["pinned_ua_bound"] = bool(share_link.pinned_ua)
        return payload

    def public_url_for_link(self, share_link: ShareLink, *, base_url: str = "") -> str:
        """Build the absolute or relative public URL for a link."""
        path = f"/share/{share_link.resource_type}/{share_link.token}"
        if not base_url:
            return path
        return base_url.rstrip("/") + path

    def cleanup_expired_links(self) -> tuple[dict, None, int]:
        """Delete expired links from the JSON store.

        Best-effort hygiene hook; not wired to a background task yet.
        """
        existing = self._store.get_share_links()
        active = [link for link in existing if not self._is_expired(link)]
        removed = len(existing) - len(active)
        if removed == 0:
            return {"removed": 0}, None, 200
        self._store.replace_share_links(active)
        return {"removed": removed}, None, 200

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_resource_reference(
        self,
        resource_type: str,
        resource_id: str,
        *,
        require_available: bool,
    ):
        if resource_type not in {"clip", "camera"}:
            return None, "Invalid resource type", 400
        if resource_type == "camera":
            if not _CAMERA_ID_RE.match(resource_id or ""):
                return None, "Invalid resource id", 400
            camera = self._store.get_camera(resource_id)
            if camera is None:
                return None, "Camera not found", 404
            return (
                {
                    "resource_name": getattr(camera, "name", "") or camera.id,
                    "camera_id": camera.id,
                },
                None,
                200,
            )

        parts = self.clip_resource_parts(resource_id)
        if parts is None:
            return None, "Invalid resource id", 400
        if require_available:
            return self._resolve_clip_resource(resource_id)
        camera_id, clip_date, filename = parts
        return (
            {
                "resource_name": self._clip_resource_name(
                    camera_id, clip_date, filename
                ),
                "camera_id": camera_id,
            },
            None,
            200,
        )

    def _resolve_clip_resource(self, resource_id: str):
        parts = self.clip_resource_parts(resource_id)
        if parts is None:
            return None, "Invalid resource id", 400
        camera_id, clip_date, filename = parts
        clip_path, error, status = self._recordings_service.resolve_clip_path(
            camera_id, clip_date, filename
        )
        if error:
            return None, self.public_resource_failure_message(), 404
        return (
            {
                "path": clip_path,
                "camera_id": camera_id,
                "clip_date": clip_date,
                "filename": filename,
                "resource_name": self._clip_resource_name(
                    camera_id, clip_date, filename
                ),
            },
            None,
            status,
        )

    def _resolve_camera_resource(self, camera_id: str):
        if not _CAMERA_ID_RE.match(camera_id or ""):
            return None, "Invalid resource id", 400
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, self.public_resource_failure_message(), 404
        if getattr(camera, "status", "") != "online":
            return None, self.public_resource_failure_message(), 404
        playlist = self._live_dir / camera_id / "stream.m3u8"
        if not playlist.is_file():
            return None, self.public_resource_failure_message(), 404
        return (
            {
                "camera_id": camera_id,
                "playlist": playlist,
                "resource_name": getattr(camera, "name", "") or camera.id,
            },
            None,
            200,
        )

    def _authorise_link(
        self,
        token: str,
        *,
        expected_type: str,
        visitor_ip: str,
        visitor_ua: str,
    ):
        link = self._store.get_share_link(token)
        if link is None:
            return None, self.public_link_failure_message(), 404
        if link.resource_type != expected_type:
            return None, self.public_link_failure_message(), 404
        if link.revoked_at:
            return None, self.public_link_failure_message(), 404
        if self._is_expired(link):
            return None, self.public_link_failure_message(), 404

        if link.pin_ip:
            if not visitor_ip:
                return None, self.public_link_failure_message(), 404
            if not link.pinned_ip:
                link.pinned_ip = visitor_ip
                self._store.save_share_link(link)
            elif not self._ip_matches(visitor_ip, link.pinned_ip):
                return None, self.public_link_failure_message(), 404

        if link.pin_ua:
            ua_signature = self._normalise_ua(visitor_ua)
            if not ua_signature:
                return None, self.public_link_failure_message(), 404
            if not link.pinned_ua:
                link.pinned_ua = ua_signature
                self._store.save_share_link(link)
            elif ua_signature != link.pinned_ua:
                return None, self.public_link_failure_message(), 404

        return link, None, 200

    def _record_access(self, link: ShareLink, visitor_ip: str) -> None:
        source = "subsequent"
        if link.access_count == 0:
            source = "first"
            link.first_access_at = _utc_now_iso()
        link.access_count += 1
        link.last_access_at = _utc_now_iso()
        self._store.save_share_link(link)
        self._log_audit(
            "SHARE_LINK_ACCESSED",
            user="public",
            ip=visitor_ip,
            detail=(
                f"token={_redact_token(link.token)} "
                f"resource={link.resource_type}:{link.resource_id} source={source}"
            ),
        )

    def _status_for_link(self, share_link: ShareLink) -> str:
        if share_link.revoked_at:
            return "revoked"
        if self._is_expired(share_link):
            return "expired"
        return "active"

    def _ttl_remaining_seconds(self, share_link: ShareLink) -> int | None:
        if not share_link.expires_at:
            return None
        expires_at = _parse_iso(share_link.expires_at)
        if expires_at is None:
            return None
        remaining = int((expires_at - _utc_now()).total_seconds())
        return max(0, remaining)

    def _is_expired(self, share_link: ShareLink) -> bool:
        if not share_link.expires_at:
            return False
        expires_at = _parse_iso(share_link.expires_at)
        if expires_at is None:
            return False
        return expires_at <= _utc_now()

    def _resource_name_for_link(self, share_link: ShareLink) -> str:
        if share_link.resource_type == "camera":
            camera = self._store.get_camera(share_link.resource_id)
            if camera is None:
                return share_link.resource_id
            return getattr(camera, "name", "") or camera.id
        parts = self.clip_resource_parts(share_link.resource_id)
        if parts is None:
            return share_link.resource_id
        return self._clip_resource_name(*parts)

    def _clip_resource_name(self, camera_id: str, clip_date: str, filename: str) -> str:
        camera_name = self._camera_name(camera_id)
        return f"{camera_name} · {clip_date} · {filename}"

    def _camera_name(self, camera_id: str) -> str:
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return camera_id
        return getattr(camera, "name", "") or camera.id

    def _device_name(self) -> str:
        settings = self._store.get_settings()
        hostname = getattr(settings, "hostname", "") or "home-monitor"
        return hostname

    def _normalise_ua(self, user_agent: str) -> str:
        ua = (user_agent or "").strip()
        if not ua:
            return ""
        lower = ua.lower()
        platform = "other"
        for candidate in ("iphone", "ipad", "android", "windows", "mac os x", "linux"):
            if candidate in lower:
                platform = candidate.replace(" ", "-")
                break

        if "edg/" in lower:
            family = "edge"
        elif "firefox/" in lower:
            family = "firefox"
        elif "chrome/" in lower and "chromium/" not in lower:
            family = "chrome"
        elif "safari/" in lower:
            family = "safari"
        else:
            family = "other"
        return f"{platform}:{family}"

    def _ip_matches(self, candidate: str, pinned: str) -> bool:
        try:
            candidate_ip = ipaddress.ip_address(candidate)
            pinned_ip = ipaddress.ip_address(pinned)
        except ValueError:
            return False
        if candidate_ip.version != pinned_ip.version:
            return False
        if candidate_ip.version == 4:
            candidate_net = ipaddress.ip_network(f"{candidate_ip}/24", strict=False)
            return pinned_ip in candidate_net
        candidate_net = ipaddress.ip_network(f"{candidate_ip}/64", strict=False)
        return pinned_ip in candidate_net

    def _public_live_mimetype(self, suffix: str) -> str:
        return {
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
            ".jpg": "image/jpeg",
        }.get(suffix, "application/octet-stream")

    def _log_audit(self, event: str, **kwargs) -> None:
        if self._audit is None:
            return
        try:
            self._audit.log_event(event, **kwargs)
        except Exception as exc:  # pragma: no cover - fail-silent service policy
            log.debug("share-link audit emit failed for %s: %s", event, exc)
