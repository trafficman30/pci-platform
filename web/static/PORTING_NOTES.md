# Phase 7.3 — MOVA popup porting notes

## errors.html (next to write)

- `body { height: 100vh }` override — same reason as messages.html (flex scroll layout breaks with pci.css min-height:100vh)
- `.logo-mark` 24px override (pci.css 28px)
- `.logo-text` 14px, no letter-spacing override
- `.section-title` **conflict** — source uses `font-family:var(--mono);font-size:9px;color:var(--red)` (fault section label). pci.css uses `font-size:11px;color:var(--muted)`. Need inline override with comment.
- `.badge` **conflict** — source `display:inline-block;font-size:9px;padding:1px 6px`. pci.css `display:inline-flex;font-size:10px;padding:2px 8px`. Need inline override with comment.
- Badge variants (`.badge-ids`, `.badge-status`, `.badge-ph`, `.badge-info`, `.badge-other`) are page-specific — keep inline.
- No toast. WebSocket `/ws/errors/{streamId}` unchanged.
- Drop: `:root`, `*`, `.logo`, `.conn-dot*`, `header`
