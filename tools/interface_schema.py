"""Emit the browser's runtime wire schema from the authoritative Python DTOs."""
import json
from pathlib import Path
import types
from typing import Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

from tsp_hpc.interface import contracts


def schema():
    definitions = {}

    def describe(kind):
        if is_typeddict(kind):
            name = kind.__name__
            if name not in definitions:
                definitions[name] = {}
                definitions[name] = {key: describe(value) for key, value in get_type_hints(kind).items()}
            return {"ref": name}
        origin, args = get_origin(kind), get_args(kind)
        if origin is Literal:
            return {"literal": list(args)}
        if origin in (Union, types.UnionType):
            return {"union": [describe(arg) for arg in args]}
        if origin is list:
            return {"list": describe(args[0])}
        if origin is dict:
            return {"map": describe(args[1])}
        return {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null", object: "json"}[kind]

    for kind in vars(contracts).values():
        if is_typeddict(kind):
            describe(kind)
    return definitions


def serialized():
    return json.dumps(schema(), sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "frontend/src/wire-schema.json"
    target.write_text(serialized())
