"""Identity of the Python implementation that actually schedules Forge recipes.

The complete-recipe scheduler does not call amber or the removed 1.0 core.
Changing its implementation must invalidate portable measurements/checkpoints,
without making an unrelated native optimizer a mandatory dependency.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ENGINE_NAME = "forge_complete_recipes_python_v1"
ENGINE_VERSION = "1.1.0b2+taichiforge.1"
SOURCE_FILES = (
    "_forge_engine.py",
    "forge_support.py",
    "forge_search_v2.py",
    "forge_report.py",
    "recipes.py",
    "search_spaces/base.py",
    "types.py",
    "utils/validation.py",
)


def scheduler_identity(source_root: Path | None = None) -> dict[str, object]:
    """Fingerprint executable scheduler inputs at the cold search boundary."""
    root = source_root if source_root is not None else Path(__file__).resolve().parent
    files = {}
    for relative in SOURCE_FILES:
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError(f"Forge scheduler source is unavailable: {relative}") from error
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        files[relative] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "files": files,
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**manifest, "source_lock": "sha256:" + hashlib.sha256(encoded).hexdigest()}
