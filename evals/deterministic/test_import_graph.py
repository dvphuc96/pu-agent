"""DETERMINISTIC EVAL — import order must not decide whether pu boots.

Reported from a clean checkout: `python -m pu connect gmail` died with

    ImportError: cannot import name 'ToolRegistry' from partially initialized
    module 'pu.tools.registry' (most likely due to a circular import)

while plain `python -m pu` (chat) kept working. The chat path only worked
by luck: its composition root imports pu.harness before pu.tools, and that
order happens to finish pu.tools.registry before anything re-enters it.

The cycle itself:

    pu.tools.registry -> pu.core.interfaces (package __init__)
    -> pu.core.interfaces.approval -> pu.approval.models (package __init__)
    -> pu.approval.executor -> pu.approval.service -> pu.harness (package
    __init__) -> pu.harness.tools -> pu.tools.registry  [partial!]

The vendor-free contract module pu.core.interfaces.approval only used
ApprovalStatus/ProposedAction in Protocol annotations, so importing the
implementation package at runtime bought nothing and cost the cycle.

That is why no test caught it: every test imports through the composition
root (harness first), so inside one pytest process the broken order and
the working order were indistinguishable. These tests therefore spawn
fresh interpreters that import pu.tools FIRST, exactly like
`pu connect gmail` / `pu connect drive` do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", ["pu.tools.gmail", "pu.tools.google_drive"])
def test_tools_first_import_survives_the_harness_cycle(module: str):
    """`pu connect <integration>` imports a pu.tools module before anything
    else touches pu.harness; a fresh interpreter must survive that order."""
    proc = subprocess.run(  # returncode IS the assertion; never raise here

        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, (
        f"{module} no longer imports on its own — the circular import is "
        f"back:\n{proc.stderr}"
    )
