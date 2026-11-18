"""FastAPI HTTP API：把 CAE 解析能力服务化（本地/内网）。"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .dataset import CaeDataset
from .pipeline import build_cae_json, slugify

DEFAULT_DATASET = os.environ.get(
    "CAX_CAE_DATASET",
    os.path.join("CAE_dataset", "Dataset_Numerical_study_on_microdroplets_interaction.zip"),
)


class ParseRequest(BaseModel):
    case: str
    with_render: bool = True
    with_caption: bool = True
    hash_members: bool = False
    data_index: int = 0


def create_app(dataset_path: str = DEFAULT_DATASET, out_dir: str = "json_out",
               access: str = "stream") -> FastAPI:
    app = FastAPI(title="CAX CAE Pipeline", version="0.1",
                  description="ANSYS Fluent CFF (.cas.h5/.dat.h5) -> 结构化 JSON")
    state: dict[str, Any] = {}

    def get_dataset() -> CaeDataset:
        ds = state.get("ds")
        if ds is None:
            ds = CaeDataset(dataset_path, access=access)
            state["ds"] = ds
        return ds

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "dataset": dataset_path}

    @app.get("/cases")
    def cases() -> dict[str, Any]:
        return get_dataset().manifest()

    @app.get("/cases/{key:path}/summary")
    def summary(key: str, data_index: int = 0) -> dict[str, Any]:
        ds = get_dataset()
        try:
            doc = build_cae_json(ds, key, out_dir=out_dir, with_render=False,
                                 with_caption=False, hash_members=False, data_index=data_index)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        content = doc["content"]
        return {
            "case": doc["source"]["dataset"]["entry"],
            "analysis": content["analysis"],
            "mesh": {k: v for k, v in content["mesh"].items() if k != "zones"},
            "zones": content["mesh"]["zones"],
            "derived": content["derived"],
            "results": content["results"],
        }

    @app.post("/parse")
    def parse(req: ParseRequest) -> dict[str, Any]:
        ds = get_dataset()
        try:
            doc = build_cae_json(ds, req.case, out_dir=out_dir, with_render=req.with_render,
                                 with_caption=req.with_caption, hash_members=req.hash_members,
                                 data_index=req.data_index)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        entry = ds.get_case(req.case)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{slugify(entry.key)}_cae.json")
        import json

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        doc["_written_to"] = path
        return doc

    return app
