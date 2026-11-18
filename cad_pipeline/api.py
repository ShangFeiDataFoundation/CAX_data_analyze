"""可选的 HTTP API（FastAPI）。依赖缺失时导入不报错。"""

from __future__ import annotations

import os
from typing import Any

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import JSONResponse
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


def create_app() -> Any:
    if not _HAS_FASTAPI:
        raise ImportError("需要 fastapi（pip install fastapi）")

    from .pipeline import parse_cad_step

    app = FastAPI(title="CAX CAD Parsing API", version="0.2")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.post("/parse_path")
    def parse_path(req: dict[str, Any]) -> dict[str, Any]:
        path = req.get("step_path")
        if not path or not os.path.isfile(path):
            raise HTTPException(400, f"文件不存在：{path}")
        out_dir = req.get("out_dir")
        try:
            res = parse_cad_step(
                path,
                out_dir=out_dir,
                with_render=req.get("with_render", False),
                with_caption=req.get("with_caption", True),
            )
            return res
        except Exception as exc:
            raise HTTPException(500, str(exc))

    @app.post("/parse_file")
    async def parse_file(file: UploadFile = File(...), out_dir: str | None = None) -> dict[str, Any]:
        os.makedirs("data", exist_ok=True)
        tmp = os.path.join("data", "_api_upload.step")
        with open(tmp, "wb") as f:
            f.write(await file.read())
        try:
            res = parse_cad_step(os.path.abspath(tmp), out_dir=out_dir or "json_out", with_render=False)
            return res
        except Exception as exc:
            raise HTTPException(500, str(exc))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
