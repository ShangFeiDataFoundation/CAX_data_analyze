"""Export the canonical Pydantic contracts as JSON Schema files."""

from __future__ import annotations

import json
from pathlib import Path

from .schema import CaeEnvelope, CadEnvelope


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "schemas"
    target.mkdir(exist_ok=True)
    for name, model in (("cad", CadEnvelope), ("cae", CaeEnvelope)):
        path = target / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(by_alias=True), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
