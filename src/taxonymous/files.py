"""Writing files so a reader never sees half of one.

The review UI reads a document's report, boxes and PDF while the worker may be writing
them; each is written beside its final name and moved into place in one step.
"""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path


def _temp(path: Path) -> Path:
    # hidden, and unique to the thread: two writers never share one
    return path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")


def replace_with(path: Path, write: Callable[[Path], None]) -> None:
    """`write(tmp)` writes the new file; it then takes `path`'s place whole."""
    path = Path(path)
    tmp = _temp(path)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    replace_with(path, lambda tmp: tmp.write_text(text))


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, indent=2) + "\n")
