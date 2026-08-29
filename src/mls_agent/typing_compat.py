"""Runtime typing compatibility needed by model backends on Python 3.10."""

from __future__ import annotations

import sys
import typing


def enable_python310_typing_compat() -> None:
    """Provide the PEP 655 typing names added to the stdlib in Python 3.11."""

    if sys.version_info >= (3, 11):
        return

    from typing_extensions import NotRequired, Required

    if not hasattr(typing, "NotRequired"):
        setattr(typing, "NotRequired", NotRequired)
    if not hasattr(typing, "Required"):
        setattr(typing, "Required", Required)
