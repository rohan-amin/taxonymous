import json
from pathlib import Path

import pytest

from tools.fixtures.generate import generate


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> tuple[Path, dict, dict]:
    """Generated fixture corpus: (directory, manifest, household profile)."""
    out = tmp_path_factory.mktemp("corpus")
    manifest = generate(out, seed=7, brokerage_pages=6)
    profile = json.loads((out / "household.json").read_text())
    return out, manifest, profile
