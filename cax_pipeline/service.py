"""Background workspace processing and index updates."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from cax_core.classification import ensure_classification
from cax_core.config import VisualSettings
from cax_core.perf import PerformanceCollector, activate, span
from cax_core.vision import ApiCaptioner, RuleBasedCaptioner

from . import reports
from .cards import build_card
from .scanner import ScanTask, scan_workspace
from .store import IndexStore


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class RunManager:
    def __init__(self, runs_root: str):
        self.runs_root = os.path.abspath(runs_root)
        os.makedirs(self.runs_root, exist_ok=True)
        self.store = IndexStore(self.runs_root)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cax-scan")
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create_run(
        self,
        workspace: str,
        *,
        with_vision: bool = True,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        timeout: int = 120,
        api_key: Optional[str] = None,
    ) -> dict[str, Any]:
        workspace_path = os.path.abspath(workspace)
        run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        trace_id = uuid.uuid4().hex
        collector = PerformanceCollector(
            run_id,
            trace_id=trace_id,
            sink=self.store.insert_performance_span,
        )
        with activate(collector):
            with collector.span("workspace.scan") as scan_span:
                tasks, summary = scan_workspace(workspace_path)
                cached: dict[str, dict[str, Any]] = {}
                pending: list[ScanTask] = []
                for task in tasks:
                    existing = reports.load_cached_report(workspace_path, task)
                    if existing:
                        cached[task.id] = existing
                    else:
                        pending.append(task)
                summary["cached_reports"] = len(cached)
                summary["stale_reports"] = sum(
                    bool(item["stale"]) for item in cached.values()
                )
                summary["pending"] = len(pending)
                scan_span.set_outputs(
                    files=len(tasks),
                    cached=len(cached),
                    pending=len(pending),
                )
        run_dir = os.path.join(self.runs_root, run_id)
        os.makedirs(os.path.join(run_dir, "records"), exist_ok=True)
        now = _now()
        run = {
            "id": run_id,
            "workspace": workspace_path,
            "run_dir": run_dir,
            "status": "queued",
            "total": len(tasks),
            "done": len(cached),
            "failed": 0,
            "skipped": 0,
            "with_vision": int(with_vision),
            "base_url": base_url,
            "model": model,
            "summary_json": json.dumps(summary, ensure_ascii=False),
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        self.store.create_run(run)
        for task in tasks:
            if task.id in cached:
                item = cached[task.id]
                fields = self._record_fields(
                    item["doc"],
                    task,
                    item["json_path"],
                    generated_at=item.get("generated_at"),
                )
                self.store.insert_record({
                    "id": task.id,
                    "run_id": run_id,
                    "status": "ok",
                    "from_cache": 1,
                    "stale": int(item["stale"]),
                    "error": None,
                    **fields,
                    "updated_at": now,
                })
            else:
                self.store.insert_record({
                    "id": task.id,
                    "run_id": run_id,
                    "kind": task.kind,
                    "format": task.format,
                    "relative_path": task.relative_path,
                    "title": task.title,
                    "status": "pending",
                    "summary": None,
                    "visual_profile_json": "null",
                    "search_text": f"{task.title} {task.relative_path} {task.format}",
                    "json_path": None,
                    "thumbnail_path": None,
                    "confidence": None,
                    "warnings_json": "[]",
                    "error": None,
                    "from_cache": 0,
                    "stale": 0,
                    "generated_at": None,
                    "card_json": "{}",
                    "updated_at": now,
                })
        cancel = threading.Event()
        with self._lock:
            self._cancel_events[run_id] = cancel
        self.executor.submit(
            self._process_run,
            run_id,
            workspace_path,
            pending,
            with_vision,
            base_url,
            model,
            timeout,
            api_key,
            cancel,
            len(cached),
            trace_id,
        )
        return self.store.get_run(run_id) or run

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        self.store.update_run(run_id, cancel_requested=1, updated_at=_now())
        return True

    def regenerate_record(
        self,
        run_id: str,
        record_id: str,
        *,
        with_vision: bool,
        base_url: str,
        model: str,
        timeout: int,
        api_key: Optional[str],
    ) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        record = self.store.get_record(run_id, record_id)
        if run is None or record is None:
            raise KeyError("记录不存在")
        if record.get("status") == "processing":
            raise RuntimeError("该文件正在重新生成")
        tasks, _summary = scan_workspace(run["workspace"])
        task = next((item for item in tasks if item.id == record_id), None)
        if task is None:
            raise KeyError("源文件已不存在或当前目录中不再可解析")
        self.store.update_record(
            run_id,
            record_id,
            status="processing",
            from_cache=0,
            error=None,
            updated_at=_now(),
        )
        self.store.update_run(run_id, status="running", updated_at=_now())
        self.executor.submit(
            self._regenerate_one,
            run_id,
            task,
            with_vision,
            base_url,
            model,
            timeout,
            api_key,
        )
        return self.store.get_record(run_id, record_id) or record

    def _regenerate_one(
        self,
        run_id: str,
        task: ScanTask,
        with_vision: bool,
        base_url: str,
        model: str,
        timeout: int,
        api_key: Optional[str],
    ) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        captioner = self._make_captioner(
            with_vision, base_url, model, timeout, api_key
        )
        collector = PerformanceCollector(
            run_id,
            sink=self.store.insert_performance_span,
        )
        collector_context = activate(collector)
        collector_context.__enter__()
        dataset = None
        try:
            if task.format == "FLUENT_CFF":
                from cae_pipeline.dataset import CaeDataset

                dataset = CaeDataset(run["workspace"])
            record_dir = os.path.join(run["run_dir"], "records", task.id)
            os.makedirs(record_dir, exist_ok=True)
            with collector.span(
                "record.regenerate",
                record_id=task.id,
                inputs={"kind": task.kind, "format": task.format},
            ):
                with span(
                    "record.parse",
                    record_id=task.id,
                    inputs={"kind": task.kind, "format": task.format},
                ) as parse_span:
                    doc = self._parse_task(
                        task, dataset, record_dir, bool(with_vision), captioner
                    )
                    parse_span.set_outputs(
                        valid=bool(
                            (doc.get("meta") or {})
                            .get("validation", {})
                            .get("is_valid")
                        )
                    )
            json_path = os.path.join(record_dir, "record.json")
            Path(json_path).write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            report_json = reports.persist_record_report(
                run["workspace"], task, record_dir
            )
            fields = self._record_fields(
                doc, task, report_json, generated_at=_now()
            )
            self.store.update_record(
                run_id,
                task.id,
                status="ok",
                from_cache=0,
                stale=0,
                **fields,
                error=None,
                updated_at=_now(),
            )
            status = "complete"
        except Exception as exc:
            self.store.update_record(
                run_id,
                task.id,
                status="failed",
                from_cache=0,
                stale=0,
                error="".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip(),
                updated_at=_now(),
            )
            status = "failed"
        finally:
            collector_context.__exit__(None, None, None)
            if dataset is not None:
                dataset.close()
            self.store.update_run(run_id, status=status, updated_at=_now())

    @staticmethod
    def _make_captioner(
        with_vision: bool,
        base_url: str,
        model: str,
        timeout: int,
        api_key: Optional[str],
    ) -> Any:
        if not with_vision:
            return None
        if api_key:
            return ApiCaptioner(VisualSettings(
                base_url=base_url,
                model=model,
                api_key=api_key,
                timeout=timeout,
                provider="deepseek",
            ))
        return RuleBasedCaptioner()

    @staticmethod
    def _record_fields(
        doc: dict[str, Any],
        task: ScanTask,
        json_path: str,
        *,
        generated_at: Optional[str],
    ) -> dict[str, Any]:
        visual = doc.get("content", {}).get("visual") or {}
        profile = visual.get("profile") or {}
        summary = visual.get("summary") or ""
        views = doc.get("content", {}).get("rendered_views") or []
        thumbnail = next(
            (
                view.get("path") for view in views
                if view.get("path")
                and str(view.get("mime_type", "")).startswith("image/")
            ),
            None,
        )
        title = RunManager._title(doc, task)
        classification = ensure_classification(doc)
        card = build_card(doc)
        search_text = " ".join([
            title,
            task.relative_path,
            task.format,
            summary,
            str(profile.get("primary_class") or ""),
            str(profile.get("display_name_zh") or ""),
            " ".join(profile.get("aliases_zh") or []),
            " ".join(profile.get("aliases_en") or []),
            " ".join(profile.get("shape_traits") or []),
            str(profile.get("search_text_zh") or ""),
            str(profile.get("search_text_en") or ""),
            " ".join(
                f"{item.get('category', '')} {item.get('label_zh', '')}"
                for item in classification.get("summary") or []
            ),
            " ".join(str(item) for item in card.get("tags") or []),
        ])
        warnings = (
            doc.get("meta", {}).get("validation", {}).get("warnings") or []
        )
        return {
            "kind": task.kind,
            "format": task.format,
            "relative_path": task.relative_path,
            "title": title,
            "summary": summary,
            "visual_profile_json": json.dumps(profile, ensure_ascii=False),
            "search_text": search_text,
            "json_path": json_path,
            "thumbnail_path": thumbnail,
            "confidence": profile.get("confidence"),
            "warnings_json": json.dumps(warnings, ensure_ascii=False),
            "generated_at": generated_at,
            "card_json": json.dumps(card, ensure_ascii=False),
            "classification_json": json.dumps(classification, ensure_ascii=False),
        }

    def _process_run(
        self,
        run_id: str,
        workspace: str,
        tasks: list[ScanTask],
        with_vision: bool,
        base_url: str,
        model: str,
        timeout: int,
        api_key: Optional[str],
        cancel: threading.Event,
        initial_done: int = 0,
        trace_id: Optional[str] = None,
    ) -> None:
        collector = PerformanceCollector(
            run_id,
            trace_id=trace_id,
            sink=self.store.insert_performance_span,
        )
        collector_context = activate(collector)
        collector_context.__enter__()
        run_span = collector.span(
            "run.process",
            inputs={
                "total": len(tasks),
                "cached": initial_done,
                "with_vision": bool(with_vision),
                "model": model,
            },
        )
        run_span.__enter__()
        dataset = None
        done = initial_done
        failed = 0
        processed = 0
        try:
            self.store.update_run(run_id, status="running", updated_at=_now())
            with_caption = bool(with_vision)
            captioner = self._make_captioner(
                with_vision, base_url, model, timeout, api_key
            )
            if any(task.format == "FLUENT_CFF" for task in tasks):
                from cae_pipeline.dataset import CaeDataset

                dataset = CaeDataset(workspace)
            for task in tasks:
                if cancel.is_set():
                    break
                self.store.update_record(
                    run_id, task.id, status="processing", error=None, updated_at=_now()
                )
                record_dir = os.path.join(self.runs_root, run_id, "records", task.id)
                os.makedirs(record_dir, exist_ok=True)
                try:
                    with span(
                        "record.parse",
                        record_id=task.id,
                        inputs={"kind": task.kind, "format": task.format},
                    ) as parse_span:
                        doc = self._parse_task(
                            task,
                            dataset,
                            record_dir,
                            with_caption,
                            captioner,
                        )
                        parse_span.set_outputs(
                            valid=bool(
                                (doc.get("meta") or {})
                                .get("validation", {})
                                .get("is_valid")
                            )
                        )
                    json_path = os.path.join(record_dir, "record.json")
                    Path(json_path).write_text(
                        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    report_json = json_path
                    try:
                        report_json = reports.persist_record_report(
                            workspace, task, record_dir
                        )
                    except Exception as report_exc:
                        warnings = doc.setdefault("meta", {}).setdefault(
                            "validation", {}
                        ).setdefault("warnings", [])
                        warnings.append(f"报告持久化失败：{report_exc}")
                        Path(json_path).write_text(
                            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                    fields = self._record_fields(
                        doc,
                        task,
                        report_json,
                        generated_at=_now(),
                    )
                    self.store.update_record(
                        run_id,
                        task.id,
                        status="ok",
                        from_cache=0,
                        stale=0,
                        **fields,
                        error=None,
                        updated_at=_now(),
                    )
                    done += 1
                    processed += 1
                except Exception as exc:
                    failed += 1
                    processed += 1
                    self.store.update_record(
                        run_id,
                        task.id,
                        status="failed",
                        error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
                        updated_at=_now(),
                    )
                self.store.update_run(
                    run_id,
                    done=done,
                    failed=failed,
                    updated_at=_now(),
                )
            status = "cancelled" if cancel.is_set() else "complete"
            skipped = max(0, len(tasks) - processed)
            self.store.update_run(
                run_id,
                status=status,
                done=done,
                failed=failed,
                skipped=skipped,
                updated_at=_now(),
            )
            run_span.set_outputs(
                done=done,
                failed=failed,
                processed=processed,
                skipped=max(0, len(tasks) - processed),
            )
        except Exception as exc:
            run_span.set_status("error", error=str(exc))
            self.store.update_run(
                run_id,
                status="failed",
                error=str(exc),
                done=done,
                failed=failed,
                updated_at=_now(),
            )
        finally:
            run_span.__exit__(None, None, None)
            collector_context.__exit__(None, None, None)
            if dataset is not None:
                dataset.close()
            with self._lock:
                self._cancel_events.pop(run_id, None)

    @staticmethod
    def _parse_task(
        task: ScanTask,
        dataset: Any,
        record_dir: str,
        with_caption: bool,
        captioner: Any,
    ) -> dict[str, Any]:
        if task.kind == "cad":
            from cad_pipeline.pipeline import build_cad_json

            return build_cad_json(
                task.path,
                out_dir=record_dir,
                with_render=True,
                with_caption=with_caption,
                captioner=captioner,
            )
        if task.format == "FLUENT_CFF":
            from cae_pipeline.pipeline import build_cae_json

            if dataset is None:
                raise RuntimeError("Fluent 数据集上下文不可用")
            return build_cae_json(
                dataset,
                task.case_key or "",
                out_dir=record_dir,
                with_render=True,
                with_caption=with_caption,
                hash_members=True,
                captioner=captioner,
            )
        if task.format == "CGNS":
            from cae_pipeline.cgns import build_cgns_json

            return build_cgns_json(
                task.path,
                out_dir=record_dir,
                with_render=True,
                with_caption=with_caption,
                captioner=captioner,
            )
        if task.format == "NASTRAN_BDF":
            from cae_pipeline.nastran import build_bdf_json

            return build_bdf_json(
                task.path,
                out_dir=record_dir,
                with_render=True,
                with_caption=with_caption,
                captioner=captioner,
            )
        if task.format == "NASTRAN_OP2":
            from cae_pipeline.op2 import build_op2_json

            return build_op2_json(
                task.path,
                out_dir=record_dir,
                with_render=True,
                with_caption=with_caption,
                captioner=captioner,
            )
        raise ValueError(f"不支持的扫描任务格式：{task.format}")

    @staticmethod
    def _title(doc: dict[str, Any], task: ScanTask) -> str:
        content = doc.get("content") or {}
        if doc.get("data_type") == "cad":
            product = content.get("product") or {}
            parts = content.get("parts") or []
            visual = content.get("visual") or {}
            visual_name = str((visual.get("profile") or {}).get("display_name_zh") or "")
            product_name = str(product.get("name") or "")
            part_name = str(parts[0].get("name") or "") if parts else ""
            unusable = {"", "unknown", "unnamed", "default", "none",
                        "open cascade shape model", "filename", "filename.step",
                        "filename.stp", "filename.iges", "filename.igs"}
            for candidate in (product_name, part_name, visual_name):
                low = candidate.strip().lower()
                if low and low not in unusable and "/" not in candidate and "\\" not in candidate:
                    return candidate
            return task.title
        visual = content.get("visual") or {}
        visual_name = str((visual.get("profile") or {}).get("display_name_zh") or "")
        if visual_name and visual_name.lower() != "unknown":
            return visual_name
        analysis = content.get("analysis") or {}
        return str(analysis.get("solver") or task.title)
