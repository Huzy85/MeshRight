"""Updates: is a newer MeshRight out?

Asks GitHub for the latest release. Only that question is sent: no files,
no details about the computer or the person. It can be switched off in the
help sheet.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import __version__

LATEST_URL = "https://api.github.com/repos/Huzy85/MeshRight/releases/latest"
RELEASES_PAGE = "https://github.com/Huzy85/MeshRight/releases"


def version_tuple(text: str) -> tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10). Anything after the numbers is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", str(text).strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str) -> bool:
    new, old = version_tuple(latest), version_tuple(current)
    width = max(len(new), len(old))
    return bool(new) and new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def check(current: str = __version__, fetch=None) -> dict:
    """{"current", "latest", "newer", "url"}; raises ValueError when GitHub
    cannot be reached."""
    fetch = fetch or _fetch
    try:
        release = fetch(LATEST_URL)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ValueError("Could not check for updates. Are you online?") from exc
    latest = str(release.get("tag_name") or "").lstrip("v")
    url = release.get("html_url") or RELEASES_PAGE
    if not url.startswith("https://github.com/"):
        url = RELEASES_PAGE
    return {"current": current, "latest": latest, "newer": is_newer(latest, current), "url": url}


def _fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"MeshRight/{__version__}",
    })
    with urllib.request.urlopen(request, timeout=6) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read(200_000))
