"""Runs tests/js/test_liveness.mjs (pure, dependency-free tests for the
core blink/liveness decision algorithm in app/static/js/liveness.js) as
part of the normal `pytest` run, so "run the test suite" stays one
command. That file drives the algorithm with synthetic EAR sequences
(a clean blink, a slow baseline drift with no real blink, a frozen/
static sequence, an outlier-resistance check, ...) directly in Node —
no browser, no face-api.js, no webcam — since the actual decision logic
has no DOM dependency at all.

Requires `node` on PATH. Skipped (not failed) if it isn't found, since
unlike everything else in this suite, this one exercises JavaScript
directly rather than through Flask's test client, and a missing optional
external tool shouldn't fail the whole run.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent / "js" / "test_liveness.mjs"


@pytest.mark.skipif(NODE is None, reason="node is not installed/on PATH")
def test_liveness_decision_algorithm():
    result = subprocess.run([NODE, str(SCRIPT)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
    assert result.returncode == 0, "see captured stdout above for which scenario(s) failed"
    assert "ALL PASS" in result.stdout
