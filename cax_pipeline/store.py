"""SQLite-backed run and record index."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

SYNONYM_GROUPS = (
    ("螺母", "螺帽", "六角螺母", "六边形螺母", "紧固件", "nut", "hex nut", "fastener"),
    ("螺栓", "螺钉", "bolt", "screw"),
    ("孔", "通孔", "中心孔", "中心通孔", "圆孔", "hole", "through hole"),
    ("六角", "六边形", "hex", "hexagonal"),
    ("支架", "托架", "bracket"),
    ("液滴", "水珠", "droplet", "drop"),
    ("流体", "流场", "CFD", "fluid", "flow"),
    ("结构", "有限元", "FEA", "structural"),
    ("应力", "von mises", "stress"),
    ("位移", "变形", "displacement", "deformation"),
    ("模态", "频率", "mode", "frequency"),
)

QUERY_SYNONYMS = {
    term: list(group)
    for group in SYNONYM_GROUPS
    for term in group
}


def query_parts(query: str) -> list[str]:
    return [item for item in re.split(r"[\s,，;；/|]+", query.strip()) if item]


def expand_query_terms(query: str) -> list[str]:
    """Expand full or partial terms through the controlled synonym groups."""
    parts = query_parts(query)
    expanded: list[str] = []
    for part in parts:
        expanded.append(part)
        low = part.lower()
        for group in SYNONYM_GROUPS:
            if any(
                low in term.lower() or term.lower() in low
                for term in group
            ):
                expanded.extend(group)
    return list(dict.fromkeys(expanded))


class IndexStore:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.path = os.path.join(self.root, "_index.sqlite3")
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    run_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    with_vision INTEGER NOT NULL DEFAULT 1,
                    base_url TEXT,
                    model TEXT,
                    summary_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    format TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    visual_profile_json TEXT,
                    search_text TEXT,
                    json_path TEXT,
                    thumbnail_path TEXT,
                    confidence REAL,
                    warnings_json TEXT,
                    error TEXT,
                    from_cache INTEGER NOT NULL DEFAULT 0,
                    stale INTEGER NOT NULL DEFAULT 0,
                    generated_at TEXT,
                    card_json TEXT,
                    classification_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_records_run ON records(run_id, status);
                CREATE TABLE IF NOT EXISTS record_categories (
                    run_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    category TEXT NOT NULL,
                    label_zh TEXT NOT NULL,
                    field_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, record_id, layer, category)
                );
                CREATE INDEX IF NOT EXISTS idx_record_categories_filter
                    ON record_categories(run_id, layer, category);
                CREATE TABLE IF NOT EXISTS perf_spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    run_id TEXT,
                    record_id TEXT,
                    operation TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    wall_ms REAL NOT NULL DEFAULT 0,
                    inputs_json TEXT,
                    outputs_json TEXT,
                    token_usage_json TEXT,
                    rates_json TEXT,
                    attributes_json TEXT,
                    retries INTEGER NOT NULL DEFAULT 0,
                    usage_complete INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_perf_spans_run
                    ON perf_spans(run_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_perf_spans_record
                    ON perf_spans(run_id, record_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_perf_spans_operation
                    ON perf_spans(run_id, operation);
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(records)").fetchall()
            }
            for name, definition in (
                ("from_cache", "INTEGER NOT NULL DEFAULT 0"),
                ("stale", "INTEGER NOT NULL DEFAULT 0"),
                ("generated_at", "TEXT"),
                ("card_json", "TEXT"),
                ("classification_json", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE records ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextlib.contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_run(self, run: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, workspace, run_dir, status, total, done, failed, skipped,
                                  with_vision, base_url, model, summary_json, error, created_at,
                                  updated_at)
                VALUES (:id, :workspace, :run_dir, :status, :total, :done, :failed, :skipped,
                        :with_vision, :base_url, :model, :summary_json, :error, :created_at,
                        :updated_at)
                """,
                run,
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["run_id"] = run_id
        assignments = ", ".join(f"{name}=:{name}" for name in fields if name != "run_id")
        with self._connection() as conn:
            conn.execute(f"UPDATE runs SET {assignments} WHERE id=:run_id", fields)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["summary"] = json.loads(data.pop("summary_json") or "{}")
        data["with_vision"] = bool(data["with_vision"])
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    def insert_record(self, record: dict[str, Any]) -> None:
        record = {
            **record,
            "from_cache": int(record.get("from_cache", 0)),
            "stale": int(record.get("stale", 0)),
            "generated_at": record.get("generated_at"),
            "card_json": record.get("card_json") or "{}",
            "classification_json": record.get("classification_json") or "{}",
        }
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO records
                (id, run_id, kind, format, relative_path, title, status, summary,
                 visual_profile_json, search_text, json_path, thumbnail_path, confidence,
                 warnings_json, error, from_cache, stale, generated_at, card_json,
                 classification_json, updated_at)
                VALUES (:id, :run_id, :kind, :format, :relative_path, :title, :status, :summary,
                        :visual_profile_json, :search_text, :json_path, :thumbnail_path,
                        :confidence, :warnings_json, :error, :from_cache, :stale,
                        :generated_at, :card_json, :classification_json, :updated_at)
                """,
                record,
            )
            self._sync_record_categories(conn, record)

    def update_record(self, run_id: str, record_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["run_id"] = run_id
        fields["record_id"] = record_id
        assignments = ", ".join(f"{name}=:{name}" for name in fields if name not in {"run_id", "record_id"})
        with self._connection() as conn:
            conn.execute(
                f"UPDATE records SET {assignments} WHERE run_id=:run_id AND id=:record_id",
                fields,
            )
            if "classification_json" in fields:
                record_id = str(fields["record_id"])
                classification = json.loads(fields.get("classification_json") or "{}")
                self._sync_record_categories(
                    conn,
                    {
                        "run_id": run_id,
                        "id": record_id,
                        "classification_json": json.dumps(
                            classification, ensure_ascii=False
                        ),
                    },
                )

    def list_records(
        self,
        run_id: str,
        status: str | None = None,
        *,
        layer: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM records WHERE run_id=?"
        params: list[Any] = [run_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        if layer or category:
            sql += (
                " AND EXISTS (SELECT 1 FROM record_categories rc"
                " WHERE rc.run_id=records.run_id AND rc.record_id=records.id"
            )
            if layer:
                sql += " AND rc.layer=?"
                params.append(layer)
            if category:
                sql += " AND rc.category=?"
                params.append(category)
            sql += ")"
        sql += " ORDER BY kind, relative_path"
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._record_dict(row) for row in rows]

    def get_record(self, run_id: str, record_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM records WHERE run_id=? AND id=?", (run_id, record_id)
            ).fetchone()
        return self._record_dict(row) if row else None

    @staticmethod
    def _record_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["visual_profile"] = json.loads(data.pop("visual_profile_json") or "null")
        data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
        data["from_cache"] = bool(data.get("from_cache"))
        data["stale"] = bool(data.get("stale"))
        data["card"] = json.loads(data.pop("card_json") or "{}")
        data["classification"] = json.loads(
            data.pop("classification_json", None) or "{}"
        )
        return data

    def search(
        self,
        run_id: str,
        query: str,
        kind: str | None = None,
        limit: int = 100,
        *,
        layer: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        records = [
            item for item in self.list_records(
                run_id,
                status="ok",
                layer=layer,
                category=category,
            )
            if not kind or item["kind"] == kind
        ]
        terms = expand_query_terms(query)
        if not terms:
            return records[:limit]

        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        lowered_terms = [term.lower() for term in terms]
        direct_terms = {term.lower() for term in query_parts(query)}
        for record in records:
            profile = record.get("visual_profile") or {}
            card = record.get("card") or {}
            classification = record.get("classification") or {}
            chunks = {
                "primary": str(profile.get("primary_class") or ""),
                "name": str(profile.get("display_name_zh") or ""),
                "aliases": " ".join(profile.get("aliases_zh") or []) + " " +
                           " ".join(profile.get("aliases_en") or []),
                "traits": " ".join([
                    *([str(item) for item in profile.get("shape_traits") or []]),
                    *([str(item) for item in card.get("tags") or []]),
                ]),
                "text": " ".join([
                    str(profile.get("search_text_zh") or ""),
                    str(profile.get("search_text_en") or ""),
                    str(record.get("search_text") or ""),
                    str(record.get("summary") or ""),
                ]),
                "path": " ".join([record.get("title") or "", record.get("relative_path") or ""]),
                "classification": " ".join([
                    str(item.get("category") or "")
                    + " "
                    + str(item.get("label_zh") or "")
                    for item in classification.get("summary") or []
                ]),
            }
            weights = {
                "primary": 12,
                "name": 10,
                "aliases": 8,
                "traits": 7,
                "text": 4,
                "path": 2,
                "classification": 6,
            }
            score = 0.0
            evidence: list[str] = []
            for term in lowered_terms:
                term_score = 0.0
                for name, text in chunks.items():
                    if term in text.lower():
                        term_score += weights[name]
                        if name in {"name", "aliases", "traits"}:
                            evidence.append(term)
                score += term_score * (2.0 if term in direct_terms else 1.0)
            if score:
                direct_evidence = [term for term in evidence if term in direct_terms]
                other_evidence = [term for term in evidence if term not in direct_terms]
                evidence = list(dict.fromkeys(direct_evidence + other_evidence))[:8]
                scored.append((score, record, evidence))
        scored.sort(key=lambda item: (-item[0], item[1]["relative_path"]))
        return [
            {**record, "score": round(score, 2), "evidence": evidence}
            for score, record, evidence in scored[:limit]
        ]

    @staticmethod
    def _sync_record_categories(
        conn: sqlite3.Connection,
        record: dict[str, Any],
    ) -> None:
        run_id = record.get("run_id")
        record_id = record.get("id") or record.get("record_id")
        if not run_id or not record_id:
            return
        conn.execute(
            "DELETE FROM record_categories WHERE run_id=? AND record_id=?",
            (run_id, record_id),
        )
        try:
            classification = json.loads(record.get("classification_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return
        for item in classification.get("summary") or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO record_categories
                (run_id, record_id, layer, category, label_zh, field_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record_id,
                    item.get("layer"),
                    item.get("category"),
                    item.get("label_zh") or item.get("category"),
                    int(item.get("field_count") or 0),
                ),
            )

    def insert_performance_span(self, event: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO perf_spans
                (span_id, trace_id, parent_span_id, run_id, record_id, operation,
                 kind, provider, model, status, started_at, wall_ms, inputs_json,
                 outputs_json, token_usage_json, rates_json, attributes_json,
                 retries, usage_complete, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("span_id"),
                    event.get("trace_id"),
                    event.get("parent_span_id"),
                    event.get("run_id"),
                    event.get("record_id"),
                    event.get("operation"),
                    event.get("kind") or "non_llm",
                    event.get("provider"),
                    event.get("model"),
                    event.get("status") or "ok",
                    event.get("started_at"),
                    float(event.get("wall_ms") or 0),
                    json.dumps(event.get("inputs") or {}, ensure_ascii=False),
                    json.dumps(event.get("outputs") or {}, ensure_ascii=False),
                    json.dumps(event.get("token_usage") or {}, ensure_ascii=False),
                    json.dumps(event.get("rates") or {}, ensure_ascii=False),
                    json.dumps(event.get("attributes") or {}, ensure_ascii=False),
                    int(event.get("retries") or 0),
                    int(bool(event.get("usage_complete"))),
                    event.get("error"),
                ),
            )

    def list_performance_spans(
        self,
        run_id: str,
        *,
        record_id: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM perf_spans WHERE run_id=?"
        params: list[Any] = [run_id]
        if record_id:
            sql += " AND record_id=?"
            params.append(record_id)
        sql += " ORDER BY started_at, rowid LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._perf_span_dict(row) for row in rows]

    @staticmethod
    def _perf_span_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in (
            "inputs",
            "outputs",
            "token_usage",
            "rates",
            "attributes",
        ):
            data[key] = json.loads(data.pop(f"{key}_json") or "{}")
        data["usage_complete"] = bool(data.get("usage_complete"))
        return data

    def performance_summary(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            return {}
        with self._connection() as conn:
            root = conn.execute(
                """
                SELECT wall_ms, trace_id FROM perf_spans
                WHERE run_id=? AND operation='run.process' AND parent_span_id IS NULL
                ORDER BY rowid DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            scan = conn.execute(
                """
                SELECT wall_ms FROM perf_spans
                WHERE run_id=? AND operation='workspace.scan'
                ORDER BY rowid DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            attempt_rows = conn.execute(
                """
                SELECT status, usage_complete, token_usage_json
                FROM perf_spans
                WHERE run_id=? AND operation='llm.visual_describe.attempt'
                """,
                (run_id,),
            ).fetchall()
            cache_hits = conn.execute(
                "SELECT COUNT(*) FROM records WHERE run_id=? AND from_cache=1",
                (run_id,),
            ).fetchone()[0]
        if root:
            elapsed_ms = float(root["wall_ms"] or 0)
            if scan:
                elapsed_ms += float(scan["wall_ms"] or 0)
        else:
            elapsed_ms = self._elapsed_since(run.get("created_at"))
            if scan:
                elapsed_ms += float(scan["wall_ms"] or 0)

        attempts = [
            {
                "status": row["status"],
                "usage_complete": bool(row["usage_complete"]),
                "token_usage": json.loads(row["token_usage_json"] or "{}"),
            }
            for row in attempt_rows
        ]
        known_tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        usage_missing = 0
        for item in attempts:
            usage = item.get("token_usage") or {}
            if not item.get("usage_complete"):
                usage_missing += 1
            for key in known_tokens:
                known_tokens[key] += int(usage.get(key) or 0)
            if "total_tokens" not in usage:
                known_tokens["total_tokens"] += int(usage.get("prompt_tokens") or 0)
                known_tokens["total_tokens"] += int(usage.get("completion_tokens") or 0)

        attempted = int(run.get("done") or 0) + int(run.get("failed") or 0)
        success_rate = (
            float(run.get("done") or 0) / attempted if attempted else 0.0
        )
        elapsed_minutes = elapsed_ms / 60000
        records_per_minute = (
            float(run.get("done") or 0) / elapsed_minutes
            if elapsed_minutes > 0
            else 0.0
        )
        return {
            "run_id": run_id,
            "trace_id": root["trace_id"] if root else None,
            "total_elapsed_ms": round(elapsed_ms, 1),
            "success_rate": round(success_rate, 4),
            "done": int(run.get("done") or 0),
            "failed": int(run.get("failed") or 0),
            "total": int(run.get("total") or 0),
            "llm_calls": len(attempts),
            "llm_failures": sum(
                item.get("status") not in {"ok", "fallback"} for item in attempts
            ),
            "total_tokens": known_tokens["total_tokens"],
            "prompt_tokens": known_tokens["prompt_tokens"],
            "completion_tokens": known_tokens["completion_tokens"],
            "cached_prompt_tokens": known_tokens["cached_prompt_tokens"],
            "usage_missing_calls": usage_missing,
            "token_usage_complete": usage_missing == 0,
            "effective_records_per_minute": round(records_per_minute, 2),
            "cache_hits": int(cache_hits),
        }

    def performance_detail(self, run_id: str) -> dict[str, Any]:
        summary = self.performance_summary(run_id)
        spans = self.list_performance_spans(run_id, limit=5000)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in spans:
            grouped.setdefault(str(item.get("operation")), []).append(item)
        operations = []
        for operation, items in grouped.items():
            durations = [float(item.get("wall_ms") or 0) for item in items]
            tokens = sum(
                int((item.get("token_usage") or {}).get("total_tokens") or 0)
                for item in items
            )
            completion_tokens = sum(
                int((item.get("token_usage") or {}).get("completion_tokens") or 0)
                for item in items
            )
            images = sum(
                int((item.get("inputs") or {}).get("images") or 0)
                for item in items
            )
            faces = sum(int((item.get("inputs") or {}).get("faces") or 0) for item in items)
            cells = sum(int((item.get("inputs") or {}).get("cells") or 0) for item in items)
            output_features = sum(
                int((item.get("outputs") or {}).get("features") or 0)
                for item in items
            )
            total_seconds = sum(durations) / 1000
            rates: dict[str, float] = {}
            if faces and total_seconds:
                rates["faces_per_second"] = round(faces / total_seconds, 2)
            if cells and total_seconds:
                rates["cells_per_second"] = round(cells / total_seconds, 2)
            if output_features and total_seconds:
                rates["features_per_second"] = round(
                    output_features / total_seconds, 2
                )
            if images and total_seconds:
                rates["images_per_second"] = round(images / total_seconds, 2)
            if completion_tokens and total_seconds:
                rates["completion_tokens_per_second"] = round(
                    completion_tokens / total_seconds, 2
                )
            operations.append({
                "operation": operation,
                "kind": items[0].get("kind"),
                "count": len(items),
                "errors": sum(item.get("status") == "error" for item in items),
                "total_ms": round(sum(durations), 2),
                "avg_ms": round(sum(durations) / len(items), 2),
                "p50_ms": round(self._percentile(durations, 0.50), 2),
                "p95_ms": round(self._percentile(durations, 0.95), 2),
                "tokens": tokens,
                "rates": rates,
            })
        operations.sort(key=lambda item: item["total_ms"], reverse=True)
        slowest = sorted(
            spans,
            key=lambda item: float(item.get("wall_ms") or 0),
            reverse=True,
        )[:20]
        return {
            "summary": summary,
            "operations": operations,
            "slowest": slowest,
            "errors": [
                item for item in spans
                if item.get("status") in {"error", "fallback", "warning"}
            ][-100:],
            "spans": spans,
        }

    def record_performance_summary(
        self,
        run_id: str,
        record_id: str,
    ) -> dict[str, Any]:
        spans = self.list_performance_spans(run_id, record_id=record_id)
        attempts = [
            item for item in spans
            if item.get("operation") == "llm.visual_describe.attempt"
        ]
        total_ms = sum(
            float(item.get("wall_ms") or 0)
            for item in spans
            if item.get("operation") == "record.parse"
        )
        stage_ms = {}
        for item in spans:
            operation = str(item.get("operation") or "")
            if operation in {
                "cad.extract_geometry",
                "cad.recognize_features",
                "cad.render_views",
                "cae.read_mesh",
                "cae.read_setup",
                "cae.derive_metrics",
                "cae.render_views",
                "visual.describe",
                "visual.rule_describe",
                "visual.rule_fallback",
            }:
                stage_ms[operation] = round(
                    stage_ms.get(operation, 0.0)
                    + float(item.get("wall_ms") or 0),
                    2,
                )
        tokens = sum(
            int((item.get("token_usage") or {}).get("total_tokens") or 0)
            for item in attempts
        )
        return {
            "total_ms": round(total_ms, 2),
            "llm_calls": len(attempts),
            "llm_failures": sum(
                item.get("status") not in {"ok", "fallback"} for item in attempts
            ),
            "tokens": tokens,
            "stage_ms": stage_ms,
            "span_count": len(spans),
        }

    @staticmethod
    def _elapsed_since(timestamp: Any) -> float:
        if not timestamp:
            return 0.0
        try:
            started = dt.datetime.fromisoformat(str(timestamp))
        except ValueError:
            return 0.0
        now = dt.datetime.now().astimezone()
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        return max(0.0, (now - started).total_seconds() * 1000)

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(
            len(ordered) - 1,
            max(0, math.ceil(quantile * len(ordered)) - 1),
        )
        return ordered[index]


def read_record_json(record: dict[str, Any]) -> dict[str, Any]:
    path = record.get("json_path")
    if not path or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
