# The Big Hop — Single-Page Build

This build is intentionally **one page**: `site/index.html`.

## Edit before deploy
Open `site/index.html` and change:
- `window.TM_CONFIG.btcAddress` (your real BTC address)
- `window.TM_CONFIG.originLabel` (where it started)
- `window.TM_CONFIG.apiBase`
  - Live (Render): `/api`
  - Static-only: `` (empty string)

## Live hosting notes (Render)
- Deploy the Flask app from `server/`
- Serve `site/` at the same domain (or place it behind the same reverse proxy)
- The page calls:
  - `GET /api/log`
  - `GET /api/stats`
  - `POST /api/submit`

