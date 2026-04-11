# ADR-0005: WebRTC for Live View, HLS as Fallback

## Status
Accepted

## Context
Live camera viewing needs sub-second latency on mobile browsers. Options:
1. HLS only (2-10s latency, universal support)
2. WebRTC only (sub-1s latency, complex setup)
3. WebRTC primary with HLS fallback

## Decision
Use MediaMTX WHEP (WebRTC) as the primary live view protocol, with HLS as automatic fallback for older browsers or restrictive networks.

## Rationale
- **Low latency**: WebRTC delivers <1s latency for security camera monitoring
- **MediaMTX handles signaling**: WHEP endpoint at :8889, no custom signaling server needed
- **HLS fallback**: HLS.js handles browsers that don't support WebRTC or when ICE fails
- **Recording stays FFmpeg**: WebRTC is for viewing only; recordings use FFmpeg segment muxer
- **NGINX proxies both**: `/webrtc/<cam-id>/` for WHEP, `/live/<cam-id>/` for HLS

## Consequences
- ICE/STUN configuration needed for non-LAN access (Phase 2 cloud relay)
- Two parallel pipelines per camera (WebRTC via MediaMTX + HLS via FFmpeg)
- Browser JS needs WebRTC→HLS fallback logic in the live view page
- UDP port 8189 must be open for WebRTC media traffic
