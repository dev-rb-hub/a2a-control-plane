"""Regenerate the Python protobuf bindings from ../../schemas/v1.

Usage (from sdk/python): python scripts/gen_proto.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT.parents[1] / "schemas" / "v1"
OUT = ROOT / "src" / "a2a_control_plane" / "_proto"

# protoc emits top-level imports between generated modules; make them package-relative.
_SIBLING_IMPORT = re.compile(r"^import (\w+_pb2) as (\w+)$", re.MULTILINE)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").touch()
    protos = sorted(str(p) for p in SCHEMAS.glob("*.proto"))
    code = protoc.main(
        ["protoc", f"-I{SCHEMAS}", f"--python_out={OUT}", f"--pyi_out={OUT}", *protos]
    )
    for generated in (*OUT.glob("*_pb2.py"), *OUT.glob("*_pb2.pyi")):
        text = generated.read_bytes().decode()
        fixed = _SIBLING_IMPORT.sub(r"from a2a_control_plane._proto import \1 as \2", text)
        if fixed != text:
            generated.write_bytes(fixed.encode())  # bytes keep LF line endings on Windows
    return code


if __name__ == "__main__":
    sys.exit(main())
