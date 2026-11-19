"""Browser runtime validation must follow the Python wire DTOs exactly."""
from pathlib import Path
import runpy


def test_generated_browser_wire_schema_matches_python_contracts():
    root = Path(__file__).resolve().parents[2]
    generator = runpy.run_path(str(root / "tools/interface_schema.py"))
    assert (root / "frontend/src/wire-schema.json").read_text() == generator["serialized"]()
    assert (root / "frontend/src/wire-types.ts").read_text() == generator["typescript"]()
