# ADR-0012: UI Architecture — HTMX + Alpine.js

**Status:** Proposed  
**Date:** 2026-04-12  
**Context:** Redesign dashboard UI for production quality while keeping server load minimal on Raspberry Pi 4B

## Decision

Keep Flask + Jinja2 server-rendered templates. Add HTMX and Alpine.js for interactivity. Do NOT adopt a SPA framework.

### Technology Choice

| Option | Bundle Size | Server Load | Complexity |
|--------|------------|-------------|------------|
| Jinja2 only (current) | 0 KB | Low | Low |
| **Jinja2 + HTMX + Alpine.js** | **~29 KB** | **Low** | **Low** |
| Preact | ~4 KB + app | Medium | Medium |
| React/Next.js | 40-130 KB+ | High (API) | High |

**HTMX** (~14 KB) handles server-driven partial updates — swap clip lists, refresh status indicators, load recording pages — without full page reloads. Flask+Jinja2 stays the rendering engine.

**Alpine.js** (~15 KB) handles client-side-only interactions — dropdown menus, modal toggles, grid/single-view switching, dark mode toggle.

Both are vendored locally (no CDN dependency on LAN).

**Rationale:** The Pi 4B serves the dashboard, the browser renders it. HTMX partial swaps mean Flask renders small HTML fragments, not full pages. Server-rendered HTML benchmarks at 92% lower time-to-interactive vs React SPAs. No build step needed (no npm, no webpack, no node on the Pi).

### Layout Design

Based on Frigate NVR (gold standard for self-hosted camera UI):

- **Dark mode default** — security dashboards are monitored in low-light
  - Dark gray (#1a1a2e), not pure black
  - Blue (#3b82f6) accent, semantic status colors (green/red/amber)
  - Light mode as toggle (localStorage preference)

- **Three pages:** Live (default), Recordings, Settings
  - Bottom tab bar on mobile, top nav bar on desktop
  - No hamburger menu (only 3-4 nav items — use visible tabs)

- **Live view:** CSS Grid with `repeat(auto-fit, minmax(300px, 1fr))`
  - Single column on phone, 2x2 on tablet/desktop
  - Tap camera to go full-view
  - WebRTC via native `<video>` element (WHEP from MediaMTX)

- **Recordings:** Horizontal date picker, timeline scrubber, clip thumbnails
  - Frigate-style scrub-to-preview

- **Status bar:** Persistent top bar with system health (CPU, disk, camera status dots)

### Performance Optimizations

- HTMX partial swaps = small HTML fragments per request
- Lazy-load snapshot thumbnails (`loading="lazy"`)
- Generate 320px thumbnails for recording lists (full snapshots on demand)
- Long `Cache-Control` headers on CSS/JS/icons
- Single WebSocket for live camera status (instead of polling)

### Mobile

- Mobile-first responsive design
- Minimum 44px tap targets
- Touch-friendly timeline scrubbing
- `auto-fit` grid eliminates most media queries

## Alternatives Considered

- **React/Next.js:** Too heavy for Pi, requires npm build step, adds operational complexity
- **Svelte:** Lighter but still needs build step and Node.js tooling
- **Vanilla JS:** Leads to spaghetti code for interactive features

## Consequences

- Dashboard loads in <1s on LAN (29 KB JS + server HTML)
- No build step — edit Jinja2 templates, refresh browser
- No Node.js dependency on Pi or in development
- Reduced server CPU usage compared to API-heavy SPA approach
