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
