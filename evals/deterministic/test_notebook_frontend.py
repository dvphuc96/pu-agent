"""Exercise notebook event/render code in Node, without browser or network dependencies."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_notebook_runtime():
    result = subprocess.run(
        [shutil.which("node"), "--test", str(Path(__file__).with_name("test_notebook_runtime.cjs"))],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
