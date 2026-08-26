from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path


RUNTIME = Path(__file__).resolve().parent / "_runtime"


@contextmanager
def case_directory():
    path = RUNTIME / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

