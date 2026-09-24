import urllib.error

import pytest

from meshright import updates


@pytest.mark.parametrize(("latest", "current", "newer"), [
    ("v0.2.0", "0.1.0", True),
    ("0.1.10", "0.1.9", True),
    ("v1.0", "1.0.0", False),
    ("0.1.0", "0.1.0", False),
    ("0.0.9", "0.1.0", False),
    ("", "0.1.0", False),
    ("nightly", "0.1.0", False),
])
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def test_check():
    release = {"tag_name": "v9.0.0", "html_url": "https://github.com/Huzy85/MeshRight/releases/tag/v9.0.0"}
    result = updates.check("0.1.0", fetch=lambda url: release)
    assert result == {"current": "0.1.0", "latest": "9.0.0", "newer": True, "url": release["html_url"]}
    # Only links to GitHub are passed on.
    odd = updates.check("0.1.0", fetch=lambda url: {"tag_name": "v9", "html_url": "https://example.com/x"})
    assert odd["url"] == updates.RELEASES_PAGE


def test_offline():
    def fail(url):
        raise urllib.error.URLError("no network")

    with pytest.raises(ValueError, match="Are you online"):
        updates.check("0.1.0", fetch=fail)
