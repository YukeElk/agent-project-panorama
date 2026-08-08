from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def schema_path(project_root: Path) -> Path:
    return project_root / "schema" / "panorama.schema.v0.1.json"


@pytest.fixture
def reference_path(project_root: Path) -> Path:
    return project_root / "examples" / "reference-project.v0.1.json"


@pytest.fixture
def template_path(project_root: Path) -> Path:
    return project_root / "templates" / "panorama.html"


@pytest.fixture
def reference_data(reference_path: Path) -> dict:
    return json.loads(reference_path.read_text(encoding="utf-8"))
