# Phase 7.3 — MOVA popup porting notes

## satflow.html (next to write)

- Add `<link rel="stylesheet" href="css/pci.css">`
- Drop: `:root`, `*`, `body`, `header` base, `.logo`, `.conn-dot` + variants
- Override: `.logo-mark { width:24px; height:24px; }`, `.logo-text { font-size:14px; letter-spacing:0; }`
- Override: `header { justify-content:space-between; padding:0 20px; position:sticky; top:0; z-index:10; }`
- **Conflict**: `.section-title` — source uses `font-family:var(--mono);font-size:9px;color:var(--muted)`. pci.css uses `font-size:11px`. Need inline override with comment.
- Keep inline: `.hdr-right`, `.stream-label`, `.content`, `.cards`, `.lane-card` + variants, `.optimiser`, `.opt-grid`, `.opt-item`, `.empty`, `.updated`, `.flow-bar*`
- **No** `height:100vh` override — page scrolls (`min-height:100vh` already in pci.css)
- WebSocket `/ws/derived/{streamId}` unchanged

---

## tma.html

- Add `<link rel="stylesheet" href="css/pci.css">`
- `body { height: 100vh; }` override (flex scroll popup)
- Drop: `:root`, `*`, `body` base, `header` base, `.logo`, `.conn-dot` + variants
- Override: `.logo-mark { width:24px; height:24px; }`, `.logo-text { font-size:14px; letter-spacing:0; }`
- Override: `header { justify-content:space-between; padding:0 20px; }`
- Keep inline: `.hdr-right`, `.stream-label`, `.content`, `.scroll`, `table`, `thead th`, `tbody tr/td`, `.stage-badge`, `.empty`, `.flow-val`, `.green-dur`
- **Dead code**: `renderCounts()` and `connectCounts()` reference `count-grid`/`period-info` DOM elements that don't exist in the HTML — leave JS as-is, do not add the missing divs
- WebSocket: `/ws/messages/{streamId}` and `/ws/tma/{streamId}` unchanged

---

## syslog.html

- **Dark-theme page** — source `:root` overrides `--bg:#0d1117`, `--text:#e6edf3` and other tokens with dark values
- **Keep `:root` inline** — it must override pci.css light-theme tokens for this page to work
- `body { height: 100vh; }` override (flex scroll popup)
- Drop: `*`, `header` base (topbar still uses `var(--topbar)` = same dark blue)
- Override: `.logo-mark { width:20px; height:20px; }`, `.logo-text { font-size:14px; letter-spacing:0; }`
- Keep inline: `.hdr-sep`, `.hdr-controls`, `select`, `input`, `.btn`, `#status`, `.log-area` (includes dark scrollbar), `.line.*`, `.ts`, `.lvl-*`, `.mod`, `.msg-*`, `.empty`
- No WebSocket — polls `GET /api/system/log?lines=N&level=X` every 3s
- No stream param — system log is not per-stream

---

## errors.html (next to write)

- `body { height: 100vh }` override — same reason as messages.html (flex scroll layout breaks with pci.css min-height:100vh)
- `.logo-mark` 24px override (pci.css 28px)
- `.logo-text` 14px, no letter-spacing override
- `.section-title` **conflict** — source uses `font-family:var(--mono);font-size:9px;color:var(--red)` (fault section label). pci.css uses `font-size:11px;color:var(--muted)`. Need inline override with comment.
- `.badge` **conflict** — source `display:inline-block;font-size:9px;padding:1px 6px`. pci.css `display:inline-flex;font-size:10px;padding:2px 8px`. Need inline override with comment.
- Badge variants (`.badge-ids`, `.badge-status`, `.badge-ph`, `.badge-info`, `.badge-other`) are page-specific — keep inline.
- No toast. WebSocket `/ws/errors/{streamId}` unchanged.
- Drop: `:root`, `*`, `.logo`, `.conn-dot*`, `header`
