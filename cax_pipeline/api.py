"""Local FastAPI prototype for scanning, indexing, and visual search."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cax_core.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    is_developer_code,
    mask_key,
    read_config,
    resolve_visual_settings,
    write_local_config,
)
from cax_core.perf import PerformanceCollector, activate

from .service import RunManager
from .reports import build_export_zip
from .store import expand_query_terms, read_record_json

STATIC_DIR = Path(__file__).resolve().parent / "static"


class RunRequest(BaseModel):
    workspace: str
    with_vision: bool = True
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=10, le=600)


class ConfigRequest(BaseModel):
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=10, le=600)
    api_key: Optional[str] = None


class RegenerateRequest(BaseModel):
    with_vision: bool = True
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=10, le=600)


def _resolve_key(
    user_key: Optional[str],
    developer_code: Optional[str],
) -> Optional[str]:
    if not user_key and not developer_code:
        configured = read_config().get("api_key")
        return str(configured) if configured else None
    if not developer_code and user_key and is_developer_code(user_key):
        developer_code = user_key
        user_key = None
    if developer_code:
        if not is_developer_code(developer_code):
            raise HTTPException(status_code=403, detail="开发者口令无效")
        key = read_config().get("api_key")
        if not key:
            raise HTTPException(status_code=400, detail="config 中未配置 api_key")
        return str(key)
    return user_key or None


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in run.items()
        if key not in {"run_dir", "base_url"}
    }


def _public_record(record: dict[str, Any], run_id: str) -> dict[str, Any]:
    key = record.get("id")
    thumbnail = record.get("thumbnail_path")
    return {
        "id": key,
        "kind": record.get("kind"),
        "format": record.get("format"),
        "relative_path": record.get("relative_path"),
        "title": record.get("title"),
        "status": record.get("status"),
        "summary": record.get("summary"),
        "visual_profile": record.get("visual_profile"),
        "card": record.get("card") or {},
        "classification": record.get("classification") or {},
        "confidence": record.get("confidence"),
        "from_cache": bool(record.get("from_cache")),
        "stale": bool(record.get("stale")),
        "generated_at": record.get("generated_at"),
        "warnings": record.get("warnings") or [],
        "error": record.get("error"),
        "score": record.get("score"),
        "evidence": record.get("evidence") or [],
        "thumbnail_url": (
            f"/api/runs/{run_id}/asset?record_id={key}&path={thumbnail}"
            if thumbnail else None
        ),
        "detail_url": f"/api/runs/{run_id}/records/{key}",
        "export_url": f"/api/runs/{run_id}/records/{key}/export",
        "regenerate_url": f"/api/runs/{run_id}/records/{key}/regenerate",
    }


def _pick_workspace_directory() -> Optional[str]:
    """Open the native Windows directory picker on the local desktop."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError(f"当前环境不支持目录选择对话框：{exc}") from exc

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askdirectory(
            parent=root,
            title="选择 CAX 工作区目录",
            mustexist=True,
        )
        return os.path.abspath(selected) if selected else None
    finally:
        root.destroy()


def create_app(runs_root: str = "data/frontend_runs") -> FastAPI:
    app = FastAPI(title="CAX Workspace Prototype", version="0.1")
    manager = RunManager(runs_root)
    app.state.manager = manager

    @app.middleware("http")
    async def disable_ui_cache(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "runs_root": os.path.abspath(runs_root)}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        config = read_config()
        settings = resolve_visual_settings()
        key = settings.api_key
        return {
            "base_url": config.get("base_url") or settings.base_url,
            "model": config.get("model") or settings.model,
            "timeout": int(config.get("timeout") or settings.timeout),
            "provider": "deepseek",
            "server_key_configured": bool(key),
            "server_key_masked": mask_key(key),
        }

    @app.post("/api/workspace/pick")
    def pick_workspace() -> dict[str, Any]:
        try:
            path = _pick_workspace_directory()
        except Exception as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"path": path, "cancelled": path is None}

    @app.put("/api/config")
    def put_config(
        request: ConfigRequest,
        x_developer_code: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "base_url": request.base_url.rstrip("/"),
            "model": request.model,
            "timeout": request.timeout,
            "provider": "deepseek",
        }
        if request.api_key:
            if not is_developer_code(x_developer_code):
                raise HTTPException(status_code=403, detail="保存服务端 Key 需要开发者口令")
            updates["api_key"] = request.api_key
        write_local_config(updates)
        return get_config()

    @app.post("/api/config/test")
    def test_config(
        request: ConfigRequest,
        x_api_key: Optional[str] = Header(default=None),
        x_developer_code: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        key = _resolve_key(x_api_key, x_developer_code)
        if not key:
            raise HTTPException(status_code=400, detail="请输入 API Key 或启用开发者模式")
        try:
            response = httpx.get(
                request.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=request.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"API 连接失败：{exc}") from exc
        return {
            "status": "ok",
            "base_url": request.base_url,
            "model": request.model,
            "key_source": "browser" if x_api_key else "config",
        }

    @app.post("/api/runs")
    def create_run(
        request: RunRequest,
        x_api_key: Optional[str] = Header(default=None),
        x_developer_code: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        if not os.path.isdir(request.workspace):
            raise HTTPException(status_code=400, detail="工作区不存在或不是目录")
        key = _resolve_key(x_api_key, x_developer_code)
        try:
            run = manager.create_run(
                request.workspace,
                with_vision=request.with_vision,
                base_url=request.base_url.rstrip("/"),
                model=request.model,
                timeout=request.timeout,
                api_key=key,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_run(run)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = manager.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        return _public_run(run)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        run = manager.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        manager.cancel(run_id)
        return _public_run(manager.store.get_run(run_id) or run)

    @app.get("/api/runs/{run_id}/records")
    def list_records(
        run_id: str,
        kind: Optional[str] = Query(default=None, pattern="^(cad|cae)$"),
        layer: Optional[str] = Query(default=None, pattern="^(data|metadata)$"),
        category: Optional[str] = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        if manager.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        records = manager.store.list_records(
            run_id,
            layer=layer,
            category=category,
        )
        if kind:
            records = [item for item in records if item["kind"] == kind]
        return {
            "items": [_public_record(item, run_id) for item in records],
            "total": len(records),
        }

    @app.get("/api/runs/{run_id}/search")
    def search(
        run_id: str,
        q: str = Query(min_length=1, max_length=200),
        kind: Optional[str] = Query(default=None, pattern="^(cad|cae)$"),
        layer: Optional[str] = Query(default=None, pattern="^(data|metadata)$"),
        category: Optional[str] = Query(default=None, max_length=64),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        if manager.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        collector = PerformanceCollector(
            run_id,
            sink=manager.store.insert_performance_span,
        )
        with activate(collector):
            with collector.span(
                "search.query",
                inputs={"query": q, "kind": kind, "layer": layer, "category": category},
            ) as search_span:
                results = manager.store.search(
                    run_id,
                    q,
                    kind=kind,
                    limit=limit,
                    layer=layer,
                    category=category,
                )
                search_span.set_outputs(results=len(results))
        return {
            "query": q,
            "items": [_public_record(item, run_id) for item in results],
            "total": len(results),
            "expanded_terms": expand_query_terms(q),
            "mode": "classification_tags_synonyms_and_text",
        }

    @app.get("/api/runs/{run_id}/performance/summary")
    def performance_summary(run_id: str) -> dict[str, Any]:
        if manager.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        return {
            **manager.store.performance_summary(run_id),
            "details_url": f"/api/runs/{run_id}/performance/details",
        }

    @app.get("/api/runs/{run_id}/performance/details")
    def performance_details(run_id: str) -> dict[str, Any]:
        if manager.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        return manager.store.performance_detail(run_id)

    @app.get("/api/runs/{run_id}/performance/export")
    def export_performance(run_id: str, format: str = Query(default="json")):
        if manager.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        if format not in {"json", "csv"}:
            raise HTTPException(status_code=400, detail="format 仅支持 json 或 csv")
        spans = manager.store.list_performance_spans(run_id)
        if format == "json":
            payload = json.dumps(spans, ensure_ascii=False, indent=2) + "\n"
            media_type = "application/json"
            suffix = "json"
        else:
            buffer = io.StringIO()
            fields = (
                "span_id", "trace_id", "parent_span_id", "run_id", "record_id",
                "operation", "kind", "provider", "model", "status", "started_at",
                "wall_ms", "prompt_tokens", "completion_tokens", "total_tokens",
                "usage_complete", "error",
            )
            writer = csv.DictWriter(buffer, fieldnames=fields)
            writer.writeheader()
            for item in spans:
                usage = item.get("token_usage") or {}
                writer.writerow({
                    **{key: item.get(key) for key in fields},
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                })
            payload = buffer.getvalue()
            media_type = "text/csv; charset=utf-8"
            suffix = "csv"
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="CAX_performance_{run_id}.{suffix}"'
                )
            },
        )

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str) -> Response:
        run = manager.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        collector = PerformanceCollector(
            run_id,
            sink=manager.store.insert_performance_span,
        )
        with activate(collector):
            with collector.span("export.run") as export_span:
                records = manager.store.list_records(run_id)
                docs = {
                    item["id"]: read_record_json(item)
                    for item in records
                }
                performance_spans = manager.store.list_performance_spans(run_id)
                payload = build_export_zip(
                    run,
                    records,
                    docs,
                    performance_spans=performance_spans,
                )
                export_span.set_outputs(
                    records=len(records),
                    bytes=len(payload),
                    spans=len(performance_spans),
                )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="CAX_report_{run_id}.zip"'
            },
        )

    @app.get("/api/runs/{run_id}/records/{record_id}/export")
    def export_record(run_id: str, record_id: str) -> Response:
        run = manager.store.get_run(run_id)
        record = manager.store.get_record(run_id, record_id)
        if run is None or record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        docs = {record_id: read_record_json(record)}
        payload = build_export_zip(
            run,
            [record],
            docs,
            single_record_id=record_id,
            performance_spans=manager.store.list_performance_spans(
                run_id,
                record_id=record_id,
            ),
        )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="CAX_report_{record_id}.zip"'
                )
            },
        )

    @app.post("/api/runs/{run_id}/records/{record_id}/regenerate")
    def regenerate_record(
        run_id: str,
        record_id: str,
        request: RegenerateRequest,
        x_api_key: Optional[str] = Header(default=None),
        x_developer_code: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        key = _resolve_key(x_api_key, x_developer_code)
        try:
            record = manager.regenerate_record(
                run_id,
                record_id,
                with_vision=request.with_vision,
                base_url=request.base_url.rstrip("/"),
                model=request.model,
                timeout=request.timeout,
                api_key=key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _public_record(record, run_id)

    @app.get("/api/runs/{run_id}/records/{record_id}")
    def record_detail(run_id: str, record_id: str) -> dict[str, Any]:
        record = manager.store.get_record(run_id, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {
            **_public_record(record, run_id),
            "json": read_record_json(record),
            "performance": manager.store.record_performance_summary(
                run_id, record_id
            ),
        }

    @app.get("/api/runs/{run_id}/asset")
    def asset(run_id: str, record_id: str, path: str) -> FileResponse:
        run = manager.store.get_run(run_id)
        record = manager.store.get_record(run_id, record_id)
        if run is None or record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        json_path = record.get("json_path")
        record_dir = (
            os.path.abspath(os.path.dirname(json_path))
            if json_path
            else os.path.abspath(os.path.join(run["run_dir"], "records", record_id))
        )
        target = os.path.abspath(os.path.join(record_dir, path))
        if os.path.commonpath([record_dir, target]) != record_dir or not os.path.isfile(target):
            raise HTTPException(status_code=404, detail="资源不存在")
        return FileResponse(target)

    return app
