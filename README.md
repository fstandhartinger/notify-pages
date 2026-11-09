# notify-pages

Private, unlisted copy pages for Florian's Telegram notification cards.

- `GET /p/<id>` serves a page stored by Sandy's `notify` (id = >= 192-bit random, URL-safe).
- Pages are uploaded with `PUT /api/pages/<id>` (Bearer `NOTIFY_PAGES_TOKEN`) and expire (max 30 days).
- No index, no directory listing, no links between pages, `robots.txt` disallows everything,
  `X-Robots-Tag: noindex, nofollow, noarchive` on every response.
- Storage is the container's ephemeral disk; Sandy re-uploads missing pages hourly
  (`notify_copypage.py sync`), so a redeploy loses nothing permanently.

This repository holds code only — never page content.

Optional `aid` and `linked_ids` metadata in the authenticated page upload add a muted card badge
and related-artifact chips. IDs are validated as six lowercase hexadecimal digits. The app badge
is a#d2497e (`artifacts.manifest.json`); badges copy their ID and need no external script or network
request. The private-page CSP and expiry behavior are preserved. Sandy's staged
`notify_copypage.py` supplies the metadata after successful card delivery and retains it on resync.

Run the local HTTP/metadata checks with `python3 -m unittest -v test_artifacts`.

## Decision pages (Nov 2026)

A page may carry `decision_n` (number of options) and a small `state` object (`PUT /api/pages/<id>` or
`PUT /api/pages/<id>/state`, Bearer). The state is injected at serve time for the page script (open / decided / closed).

`POST /api/press/<page id>` (public) only **queues** a press together with the Telegram Mini App `initData` the browser
received from Telegram. This server holds no Telegram secret, verifies nothing and decides nothing. The trusted host
fetches the queue (`GET /api/presses?wait=`, Bearer), verifies the signature and the open question itself, applies the
press once, and reports the outcome (`POST /api/presses/<press id>/result`, Bearer); the page polls
`GET /api/press/<page id>/<press id>`. Presses live in memory only, are bounded per client and per page, and expire
after 3 minutes.

Tests: `python3 -m unittest -v test_artifacts test_presses`.
