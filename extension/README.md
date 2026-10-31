# Sentinel Browser Extension

Scans the job posting you're currently viewing on LinkedIn, Indeed, or
Naukri without leaving the page — and without hitting the anti-bot
blocks that stop the server-side URL importer on those same sites when
you're logged out.

## Why this exists

`src/scraper.py` fetches a job URL server-side and reads its
schema.org `JobPosting` structured data. That works great for most
sites, but LinkedIn (and sometimes Indeed) serve a stripped-down,
login-walled page to a plain HTTP request with no session — there's
no JSON-LD to find, so the URL importer can't extract much.

This extension sidesteps that entirely: the content script runs
*inside your own browser tab*, where you're already logged in and the
page is fully rendered. It reads the exact same `JobPosting` JSON-LD
directly out of the live DOM (or falls back to the visible page text),
and sends just that to your Sentinel backend — the same
`derive_fields()` pipeline the URL importer uses handles the rest.

## How it's wired

```
content.js  (runs on the job page, reads the DOM only)
    │  chrome.runtime.sendMessage
    ▼
background.js  (service worker — makes every network call)
    │  fetch(backendUrl + "/api/...", { Authorization: Bearer <token> })
    ▼
Flask backend  (this project's app.py)
```

All network calls happen in `background.js`, never in `content.js`.
A content script's `fetch()` runs inside the *host page's* origin and
is bound by that page's Content-Security-Policy — LinkedIn's CSP
blocks connecting to `127.0.0.1:5000` outright. The background service
worker is a separate, privileged context that isn't subject to the
page's CSP, and with `host_permissions` declared in `manifest.json` its
requests bypass CORS too — so the Flask backend needs no CORS headers
added for this to work.

Since a content script can't share this app's session cookie either
(different origin), the extension authenticates with a personal API
token instead of a login session — see `/api/account/token` and the
`login_manager.request_loader` in `app.py`.

## Setup

1. Run the Sentinel backend as usual: `pip install -r requirements.txt && python app.py`
2. Log in to the web app, open the **About** tab, and click **Reveal**
   under "Browser Extension" to get your personal API token.
3. In Chrome: `chrome://extensions` → enable **Developer mode** → **Load
   unpacked** → select this `extension/` folder.
4. Click the Sentinel icon in the toolbar, paste the token (backend URL
   defaults to `http://127.0.0.1:5000`), and click **Save** — "Test
   connection" should show `Connected as <your username>`.
5. Open any job posting on LinkedIn, Indeed, or Naukri and click the
   floating **Scan with Sentinel** button.

## Files

- `manifest.json` — MV3 manifest. `host_permissions` covers localhost
  by default; `optional_host_permissions` + `chrome.permissions.request`
  in `popup.js` let you point it at a deployed backend instead.
- `background.js` — service worker; owns all backend network calls and
  extension settings (`chrome.storage.sync`).
- `content.js` — injected into job pages; extracts JobPosting JSON-LD
  (or a text fallback), renders the floating button + result panel in
  a shadow DOM so its styles never collide with the host page's.
- `popup.html` / `popup.js` — toolbar popup for backend URL + token.
- `icons/` — extension icons matching the web app's brand mark.

## Known limitations

- Sites change their markup/JSON-LD without notice — if a scan comes
  back mostly empty, use "Paste posting text" in the web app as a
  manual fallback (same as the URL importer's fallback path).
- The default `host_permissions` only cover `localhost`/`127.0.0.1`.
  Pointing the extension at a deployed backend triggers a one-time
  Chrome permission prompt (handled automatically in the popup).
