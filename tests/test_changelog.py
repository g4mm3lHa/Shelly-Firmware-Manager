"""Changelog cache: reuse a fresh, complete changelog; refetch when stale or incomplete."""

import time

import changelog
import pytest


@pytest.fixture
def store(net, monkeypatch):
    """Changelog store on tmp with a counting fake page fetch."""
    calls = []

    async def fetch(_session, url, _pins, _max):
        calls.append(url)
        return "<h2>1.5.0 2026-10-01</h2><ul><li>Fix</li></ul>"

    monkeypatch.setattr(changelog, "fetch_cloud_text", fetch)
    s = changelog.Changelogs(net.data / "changelog.json")
    s.calls = calls
    return s


def stored(s, gen, versions, age):
    """Put a stored changelog of the given age."""
    s.data[str(gen)] = {"fetched": int(time.time()) - age, "versions": {v: {"entries": []} for v in versions}}


async def test_fresh_complete_changelog_is_reused(store):
    stored(store, 2, ["1.5.0", "1.4.0"], age=600)
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert store.calls == []


async def test_stale_changelog_is_fetched(store):
    stored(store, 2, ["1.5.0"], age=25 * 3600)
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert len(store.calls) == 1


async def test_missing_version_fetched_at_most_hourly(store):
    stored(store, 2, ["1.4.0"], age=600)
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert store.calls == []  # fetched 10 min ago: the page may not list it yet
    stored(store, 2, ["1.4.0"], age=2 * 3600)
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert len(store.calls) == 1


async def test_gen1_version_format_matches(store):
    stored(store, 1, ["1.14.0"], age=600)
    await store.refresh(None, {1}, None, {1: {"20230913-112003/v1.14.0-gcb84623"}})
    assert store.calls == []


async def test_no_cache_and_clock_jump_fetch(store):
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert len(store.calls) == 1
    stored(store, 2, ["1.5.0"], age=-3600)  # clock went back
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert len(store.calls) == 2


async def test_page_is_parsed_in_a_worker_thread(store, monkeypatch):
    """3.1.0: the (up to 4 MiB) page is parsed outside the event loop thread."""
    import threading

    original, threads = changelog.parse, []

    def parse(gen, html):
        threads.append(threading.current_thread())
        return original(gen, html)

    monkeypatch.setattr(changelog, "parse", parse)
    await store.refresh(None, {2}, None, {2: {"1.5.0"}})
    assert threads and threads[0] is not threading.main_thread()
