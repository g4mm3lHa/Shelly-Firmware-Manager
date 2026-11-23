"""Changelog pages: the HTML parsers of changelog.py on pages shaped like the
official Shelly API documentation (Gen1 table below h2#changelog, Gen2+ headings
and lists) and the marks of the entries."""

import changelog

GEN1_PAGE = """
<h2 id="intro">Introduction</h2>
<table><tr><td>v9.9.9</td><td>2099-01-01</td><td>* [Shelly 1] not part of the changelog</td></tr></table>
<h2 id="changelog">Changelog</h2>
<table>
<thead><tr><th>Revision</th><th>Date</th><th>Description</th></tr></thead>
<tbody>
<tr><td>v1.14.0</td><td>2023-09-13</td><td>* [Shelly 1PM] Fix &amp; improve <b>power</b>​ metering</td></tr>
<tr><td></td><td></td><td>* [All devices] Security: updated TLS library</td></tr>
<tr><td></td><td></td><td>Line   without
scope</td></tr>
<tr><td>v1.13.0</td><td>2023-03-01</td><td>* [MQTT] New topic<script>var x = "script text";</script></td></tr>
<tr><td>v1.12.0</td></tr>
<tr><td></td><td></td><td></td></tr>
</tbody>
</table>
<script>document.write("<table><tr><td>v0.0.1</td><td>d</td><td>script text</td></tr></table>")</script>
<table><tr><td>v0.1.0</td><td>2020-01-01</td><td>* [Shelly 1] a later table</td></tr></table>
"""

GEN2_PAGE = """
<nav><h2>1.9.9</h2><ul><li>menu, not the changelog</li></ul></nav>
<article><h1>Changelog</h1>
<h2>Unreleased</h2><ul><li>not yet released</li></ul>
<h2>[1.5.0] - 2026-10-01</h2>
<h3>Fixed</h3>
<ul><li>Wi-Fi reconnect on <a href="/gen2/Devices/Gen2/ShellyPlus1PM">Plus 1PM</a>
  <ul><li>after a router restart</li></ul></li>
<li>Reported vulnerability CVE-2026-1234</li></ul>
<h3>Security</h3><ul><li>Hardened RPC<style>.x { color: red }</style></li></ul>
<h2>v1.5.0-beta1</h2><ul><li>first beta</li></ul>
</article>
<h2>1.0.0</h2><ul><li>after the article</li></ul>
"""


def test_gen1_table():
    """Only the table below h2#changelog: revision and date per version, a row
    without revision continues the version, "[scope]" is the category and a model
    only if it names a Shelly; markup, entities, zero-width characters, scripts and
    short or empty rows leave no trace."""
    assert changelog.parse(1, GEN1_PAGE) == {
        "1.14.0": {"date": "2023-09-13", "entries": [
            {"category": "Shelly 1PM", "text": "Fix & improve power metering", "models": ["Shelly 1PM"]},
            {"category": "All devices", "text": "Security: updated TLS library", "models": []},
            {"category": None, "text": "Line without scope", "models": []},
        ]},
        "1.13.0": {"date": "2023-03-01", "entries": [{"category": "MQTT", "text": "New topic", "models": []}]},
    }


def test_gen1_marks():
    """Security keywords and model references mark the entries of a Gen1 version."""
    entries = changelog.marked("1.14.0", changelog.parse(1, GEN1_PAGE)["1.14.0"])["entries"]
    assert [(e["security"], e["model_specific"]) for e in entries] == [(False, True), (True, False), (False, False)]


def test_gen2_page():
    """Gen2+: only the article after the h1; versions with and without brackets,
    date and pre-release; "Unreleased" is left out; nested list items become part
    of their entry; device links name the model; security by category or keyword."""
    versions = changelog.parse(2, GEN2_PAGE)
    assert list(versions) == ["1.5.0", "1.5.0-beta1"]
    stable = versions["1.5.0"]
    assert stable["date"] == "2026-10-01" and versions["1.5.0-beta1"]["date"] is None
    assert stable["entries"][0] == {"category": "Fixed", "text": "Wi-Fi reconnect on Plus 1PM (after a router restart)",
                                    "models": ["ShellyPlus1PM"]}
    marks = [(e["security"], e["this_model"]) for e in changelog.marked("1.5.0", stable, "Plus 1PM")["entries"]]
    assert marks == [(False, True), (True, False), (True, False)]
    assert stable["entries"][2]["text"] == "Hardened RPC"
