"""Changelogs: page fetch limits, changelog of one version (kept with the stored
firmware since 3.1.0), display between installed and offered version."""

import time

import changelog
import pytest


@pytest.fixture
def store(net, monkeypatch):
    """Changelog store on tmp with a counting fake page fetch."""
    calls = []

    async def fetch(_session, url, _pins, _max):
        calls.append(url)
        return "<h1>Changelog</h1><h2>1.5.0 2026-10-01</h2><ul><li>Fix</li></ul>"

    monkeypatch.setattr(changelog, "fetch_cloud_text", fetch)
    s = changelog.Changelogs(net.data / "changelog.json")
    s.calls = calls
    return s


def stored(s, gen, versions, age):
    """Put a stored changelog of the given age."""
    s.data[str(gen)] = {"fetched": int(time.time()) - age, "versions": {v: {"entries": []} for v in versions}}


async def test_page_fetched_recently_is_not_requested_again(store):
    """Refresh is only called for a missing changelog; a page fetched less
    than an hour ago is not asked again (it may not list the version yet)."""
    stored(store, 2, ["1.4.0"], age=600)
    await store.refresh(None, {2}, None)
    assert store.calls == []
    stored(store, 2, ["1.4.0"], age=2 * 3600)
    await store.refresh(None, {2}, None)
    assert len(store.calls) == 1
    assert store.version_entry(2, "1.5.0")["entries"][0]["text"] == "Fix"


async def test_no_cache_and_clock_jump_fetch(store):
    await store.refresh(None, {2}, None)
    assert len(store.calls) == 1
    stored(store, 2, ["1.5.0"], age=-3600)  # clock went back
    await store.refresh(None, {2}, None)
    assert len(store.calls) == 2


def test_version_entry_matches_gen1_format_and_exact_version(store):
    """The changelog of a stored firmware is found by its version number; a
    pre-release or an unknown version has none."""
    stored(store, 1, ["1.14.0"], age=600)
    stored(store, 2, ["1.5.0-beta1", "1.4.0"], age=600)
    assert store.version_entry(1, "20230913-112003/v1.14.0-gcb84623") == {"date": None, "entries": []}
    assert store.version_entry(2, "1.4.0") is not None
    assert store.version_entry(2, "1.5.0") is None  # only the beta is listed
    assert store.version_entry(2, "no version") is None and store.version_entry(1, "1.4.0") is None


def test_between_takes_the_target_from_the_store_entry(store):
    """The offered version comes from the changelog kept with the firmware;
    versions in between are added as far as the last fetched page lists them."""
    kept = {"date": "2026-10-01", "entries": [{"category": "Security", "text": "Kept with the file", "models": []}]}
    assert store.between(2, "1.4.0", "1.6.0") is None  # nothing stored at all
    only = store.between(2, "1.4.0", "1.6.0", None, kept)
    assert [v["version"] for v in only] == ["1.6.0"] and only[0]["entries"][0]["security"]
    store.data["2"] = {"fetched": int(time.time()), "versions": {
        "1.6.0": {"date": "x", "entries": [{"category": None, "text": "From the page", "models": []}]},
        "1.5.0": {"date": None, "entries": []}, "1.4.0": {"date": None, "entries": []}}}
    both = store.between(2, "1.4.0", "1.6.0", None, kept)
    assert [v["version"] for v in both] == ["1.6.0", "1.5.0"]  # newest first, installed excluded
    assert both[0]["entries"][0]["text"] == "Kept with the file"
    assert store.between(2, "1.4.0", "1.6.0")[0]["entries"][0]["text"] == "From the page"


async def test_page_is_parsed_in_a_worker_thread(store, monkeypatch):
    """The (up to 4 MiB) page is parsed outside the event loop thread."""
    import threading

    original, threads = changelog.parse, []

    def parse(gen, html):
        threads.append(threading.current_thread())
        return original(gen, html)

    monkeypatch.setattr(changelog, "parse", parse)
    await store.refresh(None, {2}, None)
    assert threads and threads[0] is not threading.main_thread()
