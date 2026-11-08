Hand-made fixtures for error paths only (timeouts are simulated in code).
Every normal-path test uses the real responses in `../real/` (the real 504
`SERVICETIMEOUT_ERROR` and key-error bodies are there too).

- `ktc_empty_page.json` — B553662 JSON page with no rows (the real empty
  search timed out on 2026-11-09, so its shape is copied from a page past
  the end of the trail app's Durunubi list).
- `ktc_result_code_99.json` — B553662 JSON body with a failing resultCode.
- `kfs_doctype.xml` — 산림청 XML with a DOCTYPE/ENTITY (refused before parsing).
- `kfs_truncated.xml` — 산림청 XML cut off in the middle of an item.
