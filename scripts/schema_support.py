"""Version-aware Panorama Schema selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_BY_VERSION = {
    "0.1": ROOT / "schema" / "panorama.schema.v0.1.json",
    "0.2": ROOT / "schema" / "panorama.schema.v0.2.json",
}


def schema_for_data(
    data: dict[str, Any], supplied: str | Path | None = None
) -> Path:
    if supplied is not None:
        return Path(supplied)
    version = str(data.get("schemaVersion", ""))
    try:
        return SCHEMA_BY_VERSION[version]
    except KeyError as exc:
        raise ValueError(f"不支持的 Panorama Schema 版本：{version!r}") from exc


__all__ = ["SCHEMA_BY_VERSION", "schema_for_data"]
