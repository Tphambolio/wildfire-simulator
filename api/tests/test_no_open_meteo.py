"""Guard: the product never calls Open-Meteo (its free tier is non-commercial; 2026-10-30).

Model weather comes from ECCC MSC GeoMet (``api/src/firesim_api/services/geomet.py``,
docs/weather-geomet.md). This fails if an Open-Meteo host appears in product code under
``frontend/src`` or ``api/src``, except on comment lines (history notes). Validation code is out
of scope on purpose: ``scripts/`` and the engine's validation harness
(``engine/src/firesim/validation/``) read the Open-Meteo ERA5 *archive* for reproducible
hindcast inputs and are not part of the live product.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_DIRS = ("frontend/src", "api/src")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".html", ".css"}
HOST = re.compile(r"open-meteo\.com", re.I)
COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")


def _offending_lines() -> list[str]:
    bad: list[str] = []
    for d in PRODUCT_DIRS:
        for path in sorted((ROOT / d).rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES or "node_modules" in path.parts:
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if HOST.search(line) and not COMMENT.match(line):
                    bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    return bad


def test_product_code_does_not_call_open_meteo():
    bad = _offending_lines()
    assert not bad, "Open-Meteo in product code (use services/geomet.py):\n" + "\n".join(bad)


def test_guard_scans_the_product_dirs():
    for d in PRODUCT_DIRS:
        assert any((ROOT / d).rglob("*.py" if d.startswith("api") else "*.ts")), d
    assert HOST.search("https://api.open-meteo.com/v1/forecast")
    assert COMMENT.match("  // Open-Meteo (api.open-meteo.com) until 2026-10-30")
