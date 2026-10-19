"""Web app routing: / sends the browser to the presentation site, which must be served from /site/ (relative ./support.js)."""
from pathlib import Path

import app


def test_root_redirects_to_site():
    r = next(r for r in app.app.routes if getattr(r, "path", None) == "/").endpoint()
    assert r.status_code == 307 and r.headers["location"] == "/site/"


def test_site_mount_serves_page_and_runtime():
    site = next(r for r in app.app.routes if getattr(r, "path", None) == "/site")
    root = Path(site.app.directory)
    assert (root / "index.html").exists() and (root / "support.js").exists()
    assert 'src="./support.js"' in (root / "index.html").read_text(encoding="utf-8")
