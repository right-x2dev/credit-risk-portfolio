"""Validate every PBIR JSON file against the official Microsoft schema named in its `$schema`.

Schemas are fetched from github.com/microsoft/json-schemas (cached under data/interim/pbir_schemas).
Usage: python scripts/validate_pbir.py [report_folder]
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "interim" / "pbir_schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/"
RAW = "https://raw.githubusercontent.com/microsoft/json-schemas/main/"


def fetch(uri: str) -> dict:
    rel = uri.replace(PREFIX, "").split("#")[0]
    path = CACHE / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(RAW + rel, timeout=30) as r:  # noqa: S310 - fixed Microsoft host
            path.write_bytes(r.read())
    return json.loads(path.read_text(encoding="utf-8"))


def retrieve(uri: str) -> Resource:
    return Resource.from_contents(fetch(uri))


def main(folder: Path) -> int:
    registry = Registry(retrieve=retrieve)
    errors = 0
    files = sorted(folder.rglob("*.json"))
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        uri = doc.get("$schema")
        if not uri or not uri.startswith(PREFIX) or "/item/report/" not in uri and "/pbip/" not in uri:
            continue
        schema = fetch(uri)
        validator = Draft7Validator({**schema, "$id": uri}, registry=registry)
        errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        for e in errs[:5]:
            errors += 1
            print(f"{f.relative_to(folder)} :: /{'/'.join(map(str, e.path))} :: {e.message[:300]}")
    print(f"checked {len(files)} files, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "powerbi"
    sys.exit(main(target))
