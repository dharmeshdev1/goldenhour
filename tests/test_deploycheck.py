"""The deploy gate: a whole site goes out, a broken or sharply smaller one does not."""

import json
from pathlib import Path

import httpx

from goldenhour.deploycheck import REQUIRED, live_summary, problems

LIVE = {"trail": {"addresses": 1000}, "stolen": {"usd_at_theft": 6.0e9}}


def site(tmp_path: Path, addresses: int = 1000, stolen: float = 6.0e9) -> Path:
    api = tmp_path / "api" / "v1"
    api.mkdir(parents=True)
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    for name in REQUIRED:
        (api / name).write_text("{}")
    (api / "summary.json").write_text(
        json.dumps({"trail": {"addresses": addresses}, "stolen": {"usd_at_theft": stolen}})
    )
    return tmp_path


def test_a_whole_site_passes(tmp_path: Path) -> None:
    assert problems(site(tmp_path, 990), LIVE) == []
    assert problems(site(tmp_path / "first"), None) == []  # nothing live yet


def test_a_site_without_the_web_app_or_with_a_broken_file_is_refused(tmp_path: Path) -> None:
    root = site(tmp_path)
    (root / "index.html").unlink()
    (root / "api" / "v1" / "feed.json").write_text("{not json")
    (root / "api" / "v1" / "graph.json").unlink()
    found = problems(root, LIVE)
    assert any("index.html" in p for p in found)
    assert any("feed.json is not readable" in p for p in found)
    assert any("graph.json is missing" in p for p in found)


def test_a_trail_that_shrank_sharply_needs_a_person(tmp_path: Path) -> None:
    root = site(tmp_path, addresses=19, stolen=1.2e9)  # what a cold start from the seeds publishes
    found = problems(root, LIVE)
    assert len(found) == 2 and "allow_shrink" in found[0]
    assert problems(root, LIVE, allow_shrink=True) == []
    assert problems(site(tmp_path / "empty", addresses=0), None) == ["summary.json has no trail addresses"]


def test_live_summary_is_none_when_absent_or_unreachable() -> None:
    def transport(answer: httpx.Response | Exception) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if isinstance(answer, Exception):
                raise answer
            return answer

        return httpx.Client(transport=httpx.MockTransport(handler))

    assert live_summary("https://x.test/api/v1", transport(httpx.Response(200, json=LIVE))) == LIVE
    assert live_summary("https://x.test/api/v1", transport(httpx.Response(404))) is None
    assert live_summary("https://x.test/api/v1", transport(httpx.ConnectError("down"))) is None
