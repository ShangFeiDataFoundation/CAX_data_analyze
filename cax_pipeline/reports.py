"""Persist per-file reports and export Markdown report packages."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .scanner import ScanTask

REPORT_DIR = ".cax_reports"
REPORT_VERSION = "1.0"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def report_root(workspace: str) -> Path:
    return Path(workspace).resolve() / REPORT_DIR


def report_dir(workspace: str, task_id: str) -> Path:
    return report_root(workspace) / "records" / task_id


def source_signature(task: ScanTask) -> list[dict[str, Any]]:
    paths = [task.path, *task.related_paths]
    signature: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        try:
            stat = path.stat()
            signature.append({
                "path": str(path.resolve()),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            })
        except OSError:
            signature.append({"path": str(path), "missing": True})
    return signature


def load_cached_report(workspace: str, task: ScanTask) -> dict[str, Any] | None:
    folder = report_dir(workspace, task.id)
    json_path = folder / "record.json"
    meta_path = folder / "meta.json"
    if not json_path.is_file() or not meta_path.is_file():
        return None
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("data_type") != task.kind:
        return None
    stored = meta.get("sources") or []
    current = source_signature(task)
    stale = stored != current or meta.get("report_version") != REPORT_VERSION
    return {
        "doc": doc,
        "json_path": str(json_path),
        "folder": str(folder),
        "stale": stale,
        "generated_at": meta.get("generated_at"),
        "meta": meta,
    }


def persist_record_report(workspace: str, task: ScanTask, record_dir: str) -> str:
    folder = report_dir(workspace, task.id)
    folder.mkdir(parents=True, exist_ok=True)
    for item in Path(record_dir).iterdir():
        target = folder / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, target)
    meta = {
        "report_version": REPORT_VERSION,
        "record_id": task.id,
        "kind": task.kind,
        "format": task.format,
        "relative_path": task.relative_path,
        "generated_at": _now(),
        "sources": source_signature(task),
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(folder / "record.json")


def _safe_text(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def record_markdown(
    record: dict[str, Any],
    doc: dict[str, Any],
    *,
    asset_prefix: str = "",
) -> str:
    content = doc.get("content") or {}
    visual = content.get("visual") or {}
    profile = visual.get("profile") or record.get("visual_profile") or {}
    classification = (
        (doc.get("meta") or {}).get("classification")
        or record.get("classification")
        or {}
    )
    warnings = (doc.get("meta") or {}).get("validation", {}).get("warnings") or []
    lines = [
        f"# {_safe_text(record.get('title') or record.get('relative_path'))}",
        "",
        f"- 文件：`{_safe_text(record.get('relative_path'))}`",
        f"- 类型：{_safe_text(record.get('kind')).upper()}",
        f"- 格式：{_safe_text(record.get('format'))}",
        f"- 状态：{_safe_text(record.get('status'))}",
        f"- 结果来源：{'已有报告' if record.get('from_cache') else '本次生成'}",
        f"- 是否过期：{'是' if record.get('stale') else '否'}",
        "",
        "## 视觉摘要",
        "",
        _safe_text(visual.get("summary") or record.get("summary") or "暂无"),
        "",
        "## 结构化标签",
        "",
        f"- 类别：{_safe_text(profile.get('display_name_zh') or profile.get('primary_class') or '未知')}",
        f"- 置信度：{profile.get('confidence', '未提供')}",
        f"- 中文别名：{'、'.join(profile.get('aliases_zh') or []) or '无'}",
        f"- 英文别名：{'、'.join(profile.get('aliases_en') or []) or '无'}",
        f"- 形状特征：{'、'.join(profile.get('shape_traits') or []) or '无'}",
        "",
    ]
    summary = classification.get("summary") or []
    if summary:
        lines += ["## 数据与元数据分类", ""]
        lines += [
            f"- {item.get('layer')} / {item.get('category')}："
            f"{item.get('label_zh') or item.get('category')}"
            f"（{item.get('field_count', 0)} 个字段）"
            for item in summary
        ]
        lines.append("")
    views = content.get("rendered_views") or []
    images = [
        item for item in views
        if item.get("path") and str(item.get("mime_type") or "").startswith("image/")
    ]
    if images:
        lines += ["## 渲染图", ""]
        for item in images:
            lines.append(f"### {_safe_text(item.get('view') or 'view')}")
            lines.append("")
            image_path = f"{asset_prefix}{item.get('path')}" if asset_prefix else item.get("path")
            lines.append(f"![{_safe_text(item.get('view'))}]({image_path})")
            lines.append("")
    if warnings:
        lines += ["## 警告", "", *[f"- {_safe_text(item)}" for item in warnings], ""]
    return "\n".join(lines).rstrip() + "\n"


def run_markdown(run: dict[str, Any], records: Iterable[dict[str, Any]],
                 docs: dict[str, dict[str, Any]]) -> str:
    record_list = list(records)
    total = len(record_list)
    cached = sum(bool(item.get("from_cache")) for item in record_list)
    failed = sum(item.get("status") == "failed" for item in record_list)
    lines = [
        "# CAX 扫描分析报告",
        "",
        f"- 工作区：`{_safe_text(run.get('workspace'))}`",
        f"- 扫描时间：{_safe_text(run.get('created_at'))}",
        f"- 文件总数：{total}",
        f"- 读取已有报告：{cached}",
        f"- 本次生成：{total - cached}",
        f"- 失败：{failed}",
        "",
        "## 文件清单",
        "",
        "| 文件 | 类型 | 格式 | 状态 | 来源 | 摘要 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in record_list:
        source = "已有报告" if item.get("from_cache") else "本次生成"
        if item.get("stale"):
            source += "（过期）"
        lines.append(
            f"| `{_safe_text(item.get('relative_path'))}` | {_safe_text(item.get('kind')).upper()} | "
            f"{_safe_text(item.get('format'))} | {_safe_text(item.get('status'))} | {source} | "
            f"{_safe_text(item.get('summary') or item.get('error'))} |"
        )
    lines.append("")
    for item in record_list:
        lines += [
            f"## {_safe_text(item.get('title') or item.get('relative_path'))}",
            "",
            record_markdown(
                item,
                docs.get(item["id"], {}),
                asset_prefix=f"records/{item['id']}/",
            ),
        ]
    return "\n".join(lines)


def performance_markdown(
    spans: list[dict[str, Any]],
    *,
    record_id: str | None = None,
) -> str:
    selected = [
        item for item in spans
        if record_id is None or item.get("record_id") == record_id
    ]
    attempts = [
        item for item in selected
        if item.get("operation") == "llm.visual_describe.attempt"
    ]
    total_tokens = sum(
        int((item.get("token_usage") or {}).get("total_tokens") or 0)
        for item in attempts
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        grouped.setdefault(str(item.get("operation")), []).append(item)
    rows = sorted(
        (
            (
                operation,
                len(items),
                sum(float(item.get("wall_ms") or 0) for item in items),
                sum(
                    int((item.get("token_usage") or {}).get("total_tokens") or 0)
                    for item in items
                ),
                sum(item.get("status") == "error" for item in items),
            )
            for operation, items in grouped.items()
        ),
        key=lambda item: item[2],
        reverse=True,
    )
    lines = [
        "# CAX 性能报告",
        "",
        f"- 性能 span：{len(selected)}",
        f"- LLM 调用：{len(attempts)}",
        f"- LLM token：{total_tokens}",
        f"- usage 缺失调用：{sum(not item.get('usage_complete') for item in attempts)}",
        "",
        "| 操作 | 次数 | 总耗时(ms) | token | 错误 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for operation, count, total_ms, tokens, errors in rows:
        lines.append(
            f"| `{_safe_text(operation)}` | {count} | {total_ms:.1f} | "
            f"{tokens} | {errors} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def build_export_zip(
    run: dict[str, Any],
    records: list[dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    *,
    single_record_id: str | None = None,
    performance_spans: list[dict[str, Any]] | None = None,
) -> bytes:
    selected = [
        item for item in records
        if single_record_id is None or item["id"] == single_record_id
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if single_record_id and selected:
            single = selected[0]
            record_spans = [
                item for item in (performance_spans or [])
                if item.get("record_id") == single_record_id
            ]
            archive.writestr("report.md", record_markdown(single, docs.get(single["id"], {})))
            archive.writestr(
                "record.json",
                json.dumps(docs.get(single["id"], {}), ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "performance.json",
                json.dumps(record_spans, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "performance.md",
                performance_markdown(record_spans, record_id=single_record_id),
            )
            base = Path(single.get("json_path") or "").parent
            for item in ((docs.get(single["id"], {}).get("content") or {}).get("rendered_views") or []):
                rel = item.get("path")
                source = base / rel if rel else None
                if source and source.is_file():
                    archive.write(source, arcname=rel)
        else:
            archive.writestr("report.md", run_markdown(run, selected, docs))
            perf = performance_spans or []
            archive.writestr(
                "performance.json",
                json.dumps(perf, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr("performance.md", performance_markdown(perf))
            for item in selected:
                rid = item["id"]
                prefix = f"records/{rid}"
                archive.writestr(
                    f"{prefix}/record.md",
                    record_markdown(item, docs.get(rid, {})),
                )
                archive.writestr(
                    f"{prefix}/record.json",
                    json.dumps(docs.get(rid, {}), ensure_ascii=False, indent=2) + "\n",
                )
                base = Path(item.get("json_path") or "").parent
                for view in ((docs.get(rid, {}).get("content") or {}).get("rendered_views") or []):
                    rel = view.get("path")
                    source = base / rel if rel else None
                    if source and source.is_file():
                        archive.write(source, arcname=f"{prefix}/{rel}")
    return buffer.getvalue()
