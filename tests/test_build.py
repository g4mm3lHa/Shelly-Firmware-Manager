"""The shipped minified UI files must be built from the current readable sources."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_ui


def test_static_files_are_current():
    """app/static/{start.js,app.js,i18n-<code>.js,style.css} match ui-src (run tools/build_ui.py otherwise)."""
    assert [n for n in build_ui.FILES if not build_ui.is_current(n)] == []


def test_index_references_versioned_assets():
    """index.html loads the assets with a version placeholder for cache busting."""
    html = (build_ui.OUT / "index.html").read_text()
    assert 'static/start.js?v=__ASSET_VERSION__"' in html
    assert 'static/style.css?v=__ASSET_VERSION__"' in html


def test_every_dictionary_is_built():
    """Each dictionary of ui-src/js is an output of its own, none is bundled into app.js."""
    assert build_ui.DICTIONARIES and all(n in build_ui.FILES for n in build_ui.DICTIONARIES)
    assert not set(build_ui.DICTIONARIES) & {p.name for p in build_ui.sources("app.js")}
