"""The committed conformance vectors must equal what the reference implementation produces."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_vectors.py"


def _exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_vectors", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORTER = _exporter()


@pytest.mark.parametrize("name", sorted(EXPORTER.VECTORS))
def test_committed_vectors_are_current(name: str) -> None:
    path = EXPORTER.OUT / f"{name}.json"
    assert path.exists(), f"missing {path}; run python scripts/export_vectors.py"
    assert path.read_text(encoding="utf-8") == EXPORTER.render(name), (
        f"{path.name} is stale; run python scripts/export_vectors.py"
    )


def test_no_stray_vector_files() -> None:
    committed = {p.stem for p in EXPORTER.OUT.glob("*.json")}
    assert committed == set(EXPORTER.VECTORS)


def test_vectors_are_valid_json_with_cases() -> None:
    for name in EXPORTER.VECTORS:
        body = json.loads((EXPORTER.OUT / f"{name}.json").read_text(encoding="utf-8"))
        assert body["vector"] == name and body["version"] == 1
