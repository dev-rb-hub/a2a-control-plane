"""Regenerate the Python protobuf bindings from ../../schemas/v1.

Usage (from sdk/python): python scripts/gen_proto.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT.parents[1] / "schemas" / "v1"
OUT = ROOT / "src" / "a2a_control_plane" / "_proto"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").touch()
    protos = sorted(str(p) for p in SCHEMAS.glob("*.proto"))
    return protoc.main(
        ["protoc", f"-I{SCHEMAS}", f"--python_out={OUT}", f"--pyi_out={OUT}", *protos]
    )


if __name__ == "__main__":
    sys.exit(main())
