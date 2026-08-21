"""Shared fixtures.

The sample floorplans are CLIENT DRAWINGS and are never committed to this
repo. Point ARCHIAGENT_FIXTURES at a directory holding them; tests that
need them skip when it is absent.
"""

import os
from pathlib import Path

import pytest

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "input-floorplans"


def fixtures_dir() -> Path:
    return Path(os.environ.get("ARCHIAGENT_FIXTURES", DEFAULT_FIXTURES))


@pytest.fixture(scope="session")
def demolition_pdf() -> Path:
    p = fixtures_dir() / "DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf"
    if not p.exists():
        pytest.skip(f"fixture not available: {p}")
    return p


@pytest.fixture(scope="session")
def ground_floor_pdf() -> Path:
    p = fixtures_dir() / "GROUND FLOOR PLAN_WORKING REVISED.pdf"
    if not p.exists():
        pytest.skip(f"fixture not available: {p}")
    return p
