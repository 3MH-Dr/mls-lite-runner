from __future__ import annotations

from typing import Any


def _normalize(value: str) -> str:
    return value.lower().replace("-", "").replace("_", "")


def select_agent_image_package(test_entries: list[dict[str, Any]], configured_files: list[dict[str, Any]]) -> str:
    """Choose the test image that owns the files exposed to the Bash Agent."""
    packages: list[str] = []
    seen: set[str] = set()
    for entry in test_entries:
        package = entry.get("package")
        normalized = _normalize(str(package))
        if package and normalized not in seen:
            seen.add(normalized)
            packages.append(str(package))
    if len(packages) == 1:
        return packages[0]
    package_by_normalized = {_normalize(package): package for package in packages}
    owners: set[str] = set()
    for entry in configured_files:
        filename = str(entry.get("filename", "")).strip("/")
        if not filename or "/" not in filename:
            continue
        owner = package_by_normalized.get(_normalize(filename.split("/", 1)[0]))
        if owner:
            owners.add(owner)
    if len(owners) == 1:
        return owners.pop()
    raise ValueError(
        "image=auto cannot select a unique Agent shell image: "
        f"test packages={packages}, file owners={sorted(owners)}; configure an explicit image"
    )
