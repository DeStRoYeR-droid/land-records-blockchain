#!/usr/bin/env python3
"""
check_web.py -- the web app's public pages and platform files, checked over HTTP.

    ./landchain.sh check-web          (the web app + gateway must be up)

Checks status codes (incl. a real 404 for an unknown route and an unknown
parcel), <title>, meta description, canonical URL, Open Graph and X card tags,
robots.txt, sitemap.xml, the web manifest, share images and the /gw proxy.
Signed-in pages are client-rendered and are covered by the browser tests described in docs/WEB.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from nodes.common import config  # noqa: E402

WEB = f"http://127.0.0.1:{config.node('web')['port']}"
passed = failed = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global passed, failed
    passed, failed = passed + ok, failed + (not ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {what}{'' if ok else '  -> ' + detail}")


def meta(html: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta[^>]+{attr}="{re.escape(name)}"[^>]+content="([^"]*)"', html) or \
        re.search(rf'<meta[^>]+content="([^"]*)"[^>]+{attr}="{re.escape(name)}"', html)
    return m.group(1) if m else None


def main() -> int:
    c = httpx.Client(base_url=WEB, timeout=30, trust_env=False)
    try:
        c.get("/api/health")
    except httpx.HTTPError:
        print(f"The web app is not answering at {WEB}. Start it with ./startup.sh --no-demo")
        return 1

    print("Public pages")
    for path in ["/", "/register", "/verify", "/how-it-works", "/agent", "/sign-in", "/officials/sign-in",
                 "/citizen/sign-in", "/citizen/register"]:
        r = c.get(path)
        html = r.text
        check(r.status_code == 200, f"{path} answers 200", str(r.status_code))
        title = re.search(r"<title>([^<]+)</title>", html)
        check(bool(title and len(title.group(1)) > 8), f"{path} has a <title>", "missing")
        desc = meta(html, "name", "description")
        check(bool(desc and 40 <= len(desc) <= 320), f"{path} has a meta description", repr(desc))
        check(bool(re.search(r'<link rel="canonical"', html)), f"{path} has a canonical URL")
        check(meta(html, "property", "og:title") is not None, f"{path} has Open Graph tags")
        check(meta(html, "name", "twitter:card") == "summary_large_image", f"{path} has an X card")
        check(bool(re.search(r"<main[ >]", html)) and "<h1" in html, f"{path} has <main> and an <h1>")
        check(bool(re.search(r'<html[^>]+lang="', html)), f"{path} declares its language")

    print("Not found")
    check(c.get("/definitely-not-a-page").status_code == 404, "unknown route is a real 404")
    r = c.get("/register/SY-NOPE-9/9")
    check(r.status_code == 404, "unknown parcel is a real 404", str(r.status_code))
    check("Go to the land register" in c.get("/definitely-not-a-page").text or "not-found" in c.get("/definitely-not-a-page").text
          or "page" in c.get("/definitely-not-a-page").text.lower(), "the 404 page is the custom one")

    print("Platform files")
    r = c.get("/robots.txt")
    check(r.status_code == 200 and "Sitemap:" in r.text and "Disallow: /officials" in r.text, "robots.txt")
    r = c.get("/sitemap.xml")
    check(r.status_code == 200 and "<urlset" in r.text and "/verify" in r.text, "sitemap.xml")
    r = c.get("/manifest.webmanifest")
    try:
        m = r.json()
        check(bool(m.get("name") and m.get("start_url") and m.get("icons")), "manifest.webmanifest")
    except json.JSONDecodeError:
        check(False, "manifest.webmanifest", "not JSON")
    for img in ["/opengraph-image", "/twitter-image", "/icon.svg", "/apple-icon.png"]:
        r = c.get(img)
        check(r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"), f"{img} is an image")

    print("Gateway proxy")
    r = c.get("/gw/api/public/summary")
    check(r.status_code == 200 and "parcels" in r.json(), "/gw/api/public/summary reaches the gateway")
    r = c.get("/gw/api/officer/summary")
    check(r.status_code == 401, "officer data needs a session", str(r.status_code))
    r = c.get("/gw/api/agent/runs")
    check(r.status_code == 401, "agent runs need an officer session", str(r.status_code))
    r = c.get("/gw/api/public/agent")
    check(r.status_code == 200 and "pgpFingerprint" in r.json(), "the validator's public status and key")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
