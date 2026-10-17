import json
from pathlib import Path

import pytest

from taxonymous.detect import ProfileMatcher
from taxonymous.profile import Profile
from taxonymous.redact import redact_pdf
from taxonymous.vault import Vault
from tools.fixtures.generate import generate


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> tuple[Path, dict, dict]:
    """Generated fixture corpus: (directory, manifest, household profile)."""
    out = tmp_path_factory.mktemp("corpus")
    manifest = generate(out, seed=7, brokerage_pages=6)
    profile = json.loads((out / "household.json").read_text())
    return out, manifest, profile


@pytest.fixture(scope="session")
def redacted(corpus, tmp_path_factory):
    """Every text fixture redacted with the household profile: ({file: (manifest doc, Result)}, vault)."""
    src, manifest, profile = corpus
    out = tmp_path_factory.mktemp("redacted")
    matcher = ProfileMatcher(Profile.from_dict(profile))
    vault = Vault(out / "vault" / "placeholders.json")
    docs = [d for d in manifest["documents"] if d["kind"] == "text"]
    results = {d["file"]: (d, redact_pdf(src / d["file"], out, matcher, vault)) for d in docs}
    return results, vault
