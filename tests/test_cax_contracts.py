from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from cax_core.classification import classify_document
from cax_core.perf import PerformanceCollector, extract_token_usage
from cax_pipeline.scanner import scan_workspace
from cax_pipeline.store import IndexStore
from cax_core.vision import normalize_profile

ROOT = Path(__file__).resolve().parents[1]


class SchemaContractTests(unittest.TestCase):
    def test_all_foreign_formats_share_cae_content_shape(self):
        from cae_pipeline.cgns import build_cgns_json
        from cae_pipeline.nastran import build_bdf_json
        from cae_pipeline.op2 import build_op2_json

        cgns = next(ROOT.glob("*/cgns_examples/bump_hdf5.cgns.gz"))
        bdf = next(ROOT.glob("*/pynastran/freedlm.bdf"))
        op2 = next(ROOT.glob("*/pynastran/op2_extra/plate.op2"))
        docs = [
            build_cgns_json(str(cgns), with_render=False, with_caption=False),
            build_bdf_json(str(bdf), with_render=False, with_caption=False),
            build_op2_json(str(op2), with_render=False, with_caption=False),
        ]
        expected = {
            "analysis", "mesh", "setup", "field_catalog", "results",
            "zone_results", "derived", "rendered_views", "visual",
        }
        for doc in docs:
            self.assertEqual(doc["schema_version"], "2.1")
            self.assertEqual(doc["data_type"], "cae")
            self.assertTrue(doc["meta"]["validation"]["is_valid"])
            self.assertEqual(
                doc["meta"]["classification"]["taxonomy_version"],
                "cax-classification/1.0",
            )
            self.assertEqual(set(doc["content"]), expected)
            self.assertEqual(
                set(doc["source"]),
                {"primary_format", "files", "parser", "parsed_at", "solver", "dataset"},
            )

    def test_cad_formats_share_cad_content_shape(self):
        from cad_pipeline.pipeline import build_cad_json

        step = next((ROOT / "data" / "abc_step" / "extracted").rglob("*.step"))
        iges = next((ROOT / "data" / "iges_selfmade").glob("*.igs"))
        for model in (step, iges):
            doc = build_cad_json(str(model), with_render=False, with_caption=False)
            self.assertEqual(doc["schema_version"], "2.1")
            self.assertEqual(doc["data_type"], "cad")
            self.assertTrue(doc["meta"]["validation"]["is_valid"])
            self.assertTrue(doc["meta"]["classification"]["facets"])
            legacy = json.loads(json.dumps(doc, ensure_ascii=False))
            legacy["schema_version"] = "2.0"
            legacy["meta"].pop("classification", None)
            from cax_core.schema import validate_cad

            self.assertEqual(validate_cad(legacy).data_type, "cad")
            self.assertEqual(
                set(doc["content"]),
                {"product", "parts", "assembly", "rendered_views", "visual"},
            )

    def test_fluent_cff_uses_the_same_cae_contract(self):
        from cae_pipeline.dataset import CaeDataset
        from cae_pipeline.pipeline import build_cae_json

        source = next((ROOT / "CAE_dataset").glob("*.zip"))
        dataset = CaeDataset(str(source))
        try:
            doc = build_cae_json(
                dataset,
                "K86_S01",
                with_render=False,
                with_caption=False,
                hash_members=False,
            )
        finally:
            dataset.close()
        self.assertEqual(doc["schema_version"], "2.1")
        self.assertTrue(doc["meta"]["validation"]["is_valid"])
        self.assertEqual(doc["source"]["primary_format"], "FLUENT_CFF")
        self.assertTrue(doc["meta"]["classification"]["summary"])
        self.assertEqual(
            set(doc["content"]),
            {
                "analysis", "mesh", "setup", "field_catalog", "results",
                "zone_results", "derived", "rendered_views", "visual",
            },
        )


class SearchTests(unittest.TestCase):
    def test_nut_synonyms_match_shape_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IndexStore(tmp)
            now = "2026-10-23T00:00:00+08:00"
            store.create_run({
                "id": "r1", "workspace": tmp, "run_dir": tmp, "status": "complete",
                "total": 1, "done": 1, "failed": 0, "skipped": 0, "with_vision": 1,
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "summary_json": "{}", "error": None, "created_at": now, "updated_at": now,
            })
            profile = normalize_profile({
                "primary_class": "nut",
                "display_name_zh": "六角螺母",
                "aliases_zh": ["螺母", "螺帽"],
                "aliases_en": ["hex nut"],
                "shape_traits": ["六边形外轮廓", "中心通孔"],
            })
            store.insert_record({
                "id": "rec1", "run_id": "r1", "kind": "cad", "format": "STEP",
                "relative_path": "parts/a.step", "title": "A", "status": "ok",
                "summary": "六角螺母", "visual_profile_json": json.dumps(profile, ensure_ascii=False),
                "search_text": "六角螺母", "json_path": None, "thumbnail_path": None,
                "confidence": 0.9, "warnings_json": "[]", "error": None, "updated_at": now,
            })
            results = store.search("r1", "螺母")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["id"], "rec1")

    def test_single_character_nut_prefix_expands_synonyms(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IndexStore(tmp)
            now = "2026-10-30T00:00:00+08:00"
            store.create_run({
                "id": "r1", "workspace": tmp, "run_dir": tmp, "status": "complete",
                "total": 1, "done": 1, "failed": 0, "skipped": 0, "with_vision": 1,
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "summary_json": "{}", "error": None, "created_at": now, "updated_at": now,
            })
            store.insert_record({
                "id": "rec1", "run_id": "r1", "kind": "cad", "format": "STEP",
                "relative_path": "parts/a.step", "title": "六角螺母", "status": "ok",
                "summary": "紧固件", "visual_profile_json": json.dumps({
                    "primary_class": "nut",
                    "display_name_zh": "六角螺母",
                    "aliases_zh": [],
                    "aliases_en": [],
                    "shape_traits": ["六边形外轮廓", "中心通孔"],
                }, ensure_ascii=False),
                "search_text": "六角螺母 六边形外轮廓 中心通孔", "json_path": None,
                "thumbnail_path": None, "confidence": 0.99, "warnings_json": "[]",
                "error": None, "updated_at": now,
            })
            results = store.search("r1", "螺")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["id"], "rec1")


class CardTests(unittest.TestCase):
    def test_cad_and_cae_cards_include_readable_metrics(self):
        from cax_pipeline.cards import build_card

        cad = build_card({
            "data_type": "cad",
            "content": {
                "product": {"units": "MM"},
                "parts": [{
                    "bbox": {"xmin": 0, "ymin": 0, "zmin": 0,
                             "xmax": 100, "ymax": 60, "zmax": 20},
                    "topology": {"faces": 7, "solids": 1},
                    "mass_props": {"volume": 120000, "surface_area": 20000},
                    "features": [{
                        "type": "cylindrical",
                        "params": {"subtype": "hole", "radius": 8},
                    }],
                    "face_types": [{"type": "PLANE", "count": 6}],
                }],
                "visual": {"profile": {"shape_traits": ["中心通孔"]}},
            },
        })
        self.assertIn("尺寸", {item["label"] for item in cad["metrics"]})
        self.assertIn("孔特征", {item["label"] for item in cad["metrics"]})
        self.assertIn("中心通孔", cad["tags"])

        cae = build_card({
            "data_type": "cae",
            "content": {
                "analysis": {
                    "physics": "multiphase_flow",
                    "solver": "ANSYS Fluent 25.1",
                    "models": {
                        "turbulence": "k-omega SST",
                        "multiphase": {"enabled": True, "model": "VOF", "phase_count": 3},
                    },
                    "time": {"transient": True},
                    "phases": [],
                },
                "mesh": {"cells": 1521243, "nodes": 930591, "zones": {"wall": {}},
                         "cell_types": {"tet": 860900}},
                "results": [{}, {}],
                "derived": {"droplet": {"spreading_ratio": 0.9993,
                                        "diameter_m": 9.99e-6}},
            },
        })
        values = {item["label"]: item["value"] for item in cae["metrics"]}
        self.assertIn("1.52M 单元", values["网格"])
        self.assertEqual(values["结果场"], "2 个")
        self.assertEqual(values["铺展系数 D/D0"], "0.999")
        self.assertTrue(any("VOF" in tag for tag in cae["tags"]))


class ClassificationTests(unittest.TestCase):
    def test_geometry_material_and_source_are_distinct_facets(self):
        cad = {
            "schema_version": "2.1",
            "data_type": "cad",
            "source": {
                "primary_format": "STEP",
                "files": [{"role": "model", "path": "a.step"}],
            },
            "meta": {},
            "content": {
                "product": {"units": "MM"},
                "parts": [{
                    "name": "bracket",
                    "bbox": {},
                    "mass_props": {"volume": 12.5, "surface_area": 40.0},
                    "topology": {},
                    "features": [],
                }],
            },
        }
        cad_classification = classify_document(cad)
        cad_facets = {item["key"]: item for item in cad_classification["facets"]}
        self.assertEqual(
            cad_facets["cad.part.0.volume"]["category"],
            "geometry",
        )
        self.assertEqual(cad_facets["cad.part.0.volume"]["origin"], "computed")
        self.assertEqual(
            cad_facets["source.file.0.path"]["layer"],
            "metadata",
        )

        cae = {
            "schema_version": "2.1",
            "data_type": "cae",
            "source": {"files": []},
            "meta": {},
            "content": {
                "analysis": {"phases": []},
                "setup": {"materials": {"aluminium": {"density": 2700}}},
                "mesh": {},
                "results": [],
            },
        }
        cae_classification = classify_document(cae)
        cae_facets = {item["key"]: item for item in cae_classification["facets"]}
        self.assertEqual(cae_facets["cae.materials"]["category"], "material")
        self.assertEqual(cae_facets["cae.materials"]["origin"], "declared")
        self.assertNotEqual(
            cad_facets["cad.part.0.volume"]["category"],
            cae_facets["cae.materials"]["category"],
        )


class PerformanceTests(unittest.TestCase):
    def test_provider_usage_is_not_invented(self):
        usage, complete = extract_token_usage({
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        })
        self.assertTrue(complete)
        self.assertEqual(usage["total_tokens"], 150)
        self.assertEqual(usage["cached_prompt_tokens"], 20)
        missing, missing_complete = extract_token_usage({"usage": {}})
        self.assertEqual(missing, {})
        self.assertFalse(missing_complete)

    def test_performance_spans_preserve_parent_and_tokens(self):
        events = []
        collector = PerformanceCollector("run1", sink=events.append)
        with collector.span("record.parse", record_id="rec1") as parent:
            with collector.span("llm.visual_describe.attempt", kind="llm") as child:
                child.set_token_usage(
                    {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                    complete=True,
                )
            parent.set_outputs(valid=True)
        child_event = next(item for item in events if item["kind"] == "llm")
        self.assertEqual(child_event["record_id"], "rec1")
        self.assertTrue(child_event["parent_span_id"])
        self.assertEqual(child_event["token_usage"]["total_tokens"], 14)
        self.assertTrue(child_event["usage_complete"])

    def test_store_aggregates_performance_and_filters_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IndexStore(tmp)
            now = "2026-11-18T00:00:00+08:00"
            store.create_run({
                "id": "r1", "workspace": tmp, "run_dir": tmp, "status": "complete",
                "total": 1, "done": 1, "failed": 0, "skipped": 0, "with_vision": 1,
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "summary_json": "{}", "error": None, "created_at": now, "updated_at": now,
            })
            store.insert_record({
                "id": "rec1", "run_id": "r1", "kind": "cad", "format": "STEP",
                "relative_path": "a.step", "title": "A", "status": "ok",
                "summary": "", "visual_profile_json": "null", "search_text": "A",
                "json_path": None, "thumbnail_path": None, "confidence": None,
                "warnings_json": "[]", "error": None, "updated_at": now,
                "classification_json": json.dumps({
                    "taxonomy_version": "cax-classification/1.0",
                    "facets": [],
                    "summary": [{
                        "layer": "data",
                        "category": "geometry",
                        "label_zh": "几何度量",
                        "field_count": 2,
                    }],
                }, ensure_ascii=False),
            })
            self.assertEqual(len(store.list_records("r1", category="geometry")), 1)
            self.assertEqual(len(store.list_records("r1", category="material")), 0)

            store.insert_performance_span({
                "span_id": "root", "trace_id": "trace", "parent_span_id": None,
                "run_id": "r1", "record_id": None, "operation": "run.process",
                "kind": "non_llm", "provider": None, "model": None, "status": "ok",
                "started_at": now, "wall_ms": 1000, "inputs": {}, "outputs": {},
                "token_usage": {}, "rates": {}, "attributes": {}, "retries": 0,
                "usage_complete": False, "error": None,
            })
            store.insert_performance_span({
                "span_id": "llm", "trace_id": "trace", "parent_span_id": "root",
                "run_id": "r1", "record_id": "rec1",
                "operation": "llm.visual_describe.attempt", "kind": "llm",
                "provider": "deepseek", "model": "deepseek-flash", "status": "ok",
                "started_at": now, "wall_ms": 500, "inputs": {}, "outputs": {},
                "token_usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                },
                "rates": {}, "attributes": {}, "retries": 0,
                "usage_complete": True, "error": None,
            })
            summary = store.performance_summary("r1")
            self.assertEqual(summary["total_elapsed_ms"], 1000)
            self.assertEqual(summary["llm_calls"], 1)
            self.assertEqual(summary["total_tokens"], 150)
            self.assertEqual(summary["effective_records_per_minute"], 60)

    def test_performance_api_exposes_summary_details_and_export(self):
        from fastapi.testclient import TestClient

        from cax_pipeline.api import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(str(Path(tmp) / "runs"))
            store = app.state.manager.store
            now = "2026-11-18T00:00:00+08:00"
            store.create_run({
                "id": "r1", "workspace": tmp, "run_dir": tmp, "status": "complete",
                "total": 1, "done": 1, "failed": 0, "skipped": 0, "with_vision": 1,
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "summary_json": "{}", "error": None, "created_at": now, "updated_at": now,
            })
            store.insert_performance_span({
                "span_id": "root", "trace_id": "trace", "parent_span_id": None,
                "run_id": "r1", "record_id": None, "operation": "run.process",
                "kind": "non_llm", "provider": None, "model": None, "status": "ok",
                "started_at": now, "wall_ms": 2000, "inputs": {}, "outputs": {},
                "token_usage": {}, "rates": {}, "attributes": {}, "retries": 0,
                "usage_complete": False, "error": None,
            })
            with TestClient(app) as client:
                summary = client.get("/api/runs/r1/performance/summary")
                details = client.get("/api/runs/r1/performance/details")
                exported = client.get("/api/runs/r1/performance/export?format=json")
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.json()["total_elapsed_ms"], 2000)
            self.assertIn("details_url", summary.json())
            self.assertEqual(details.status_code, 200)
            self.assertTrue(details.json()["spans"])
            self.assertEqual(exported.status_code, 200)
            self.assertIn("application/json", exported.headers["content-type"])

    def test_run_manager_records_root_and_record_spans(self):
        import threading

        from cax_pipeline.service import RunManager
        from cax_pipeline.scanner import ScanTask

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            source = workspace / "part.step"
            source.write_text("STEP", encoding="utf-8")
            manager = RunManager(str(Path(tmp) / "runs"))
            now = "2026-11-18T00:00:00+08:00"
            manager.store.create_run({
                "id": "r1", "workspace": str(workspace),
                "run_dir": str(Path(tmp) / "runs" / "r1"), "status": "queued",
                "total": 1, "done": 0, "failed": 0, "skipped": 0, "with_vision": 0,
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "summary_json": "{}", "error": None, "created_at": now, "updated_at": now,
            })
            task = ScanTask(
                id="rec1",
                kind="cad",
                format="STEP",
                path=str(source),
                relative_path="part.step",
                title="part.step",
            )
            doc = {
                "schema_version": "2.1",
                "data_type": "cad",
                "source": {"files": []},
                "meta": {
                    "validation": {"is_valid": True, "warnings": [], "errors": []},
                },
                "content": {
                    "product": {"units": "MM"},
                    "parts": [{
                        "name": "part",
                        "bbox": {},
                        "mass_props": {"volume": 1, "surface_area": 6},
                        "topology": {},
                        "features": [],
                    }],
                    "assembly": {"instances": []},
                    "rendered_views": [],
                    "visual": None,
                },
            }
            with patch.object(manager, "_parse_task", return_value=doc):
                manager._process_run(
                    "r1",
                    str(workspace),
                    [task],
                    False,
                    "https://api.deepseek.com",
                    "deepseek-flash",
                    120,
                    None,
                    threading.Event(),
                )
            spans = manager.store.list_performance_spans("r1")
            self.assertTrue(any(item["operation"] == "run.process" for item in spans))
            self.assertTrue(any(item["operation"] == "record.parse" for item in spans))
            self.assertEqual(manager.store.get_run("r1")["status"], "complete")
            manager.executor.shutdown(wait=False)


class ScannerTests(unittest.TestCase):
    def test_adf_cgns_is_not_a_parseable_task(self):
        tasks, summary = scan_workspace(str(ROOT))
        cgns = [item for item in tasks if item.format == "CGNS"]
        self.assertTrue(cgns)
        self.assertFalse(any(item.path.endswith("tut21.cgns.gz") for item in cgns))
        self.assertTrue(summary["skipped_unsupported"])

    def test_structured_cgns_zone_gets_drawable_boundary_faces(self):
        from cae_pipeline.cgns import CgnsFile
        from cae_pipeline.preview import _structured_boundary_elements

        source = ROOT / "test_workspace" / "cae" / "cgns" / "bump_hdf5.cgns.gz"
        cg = CgnsFile(str(source))
        try:
            for _base_name, base in cg.bases():
                for zone_name, zone in cg.zones(base):
                    xyz, elements = cg.zone_arrays(zone)
                    info = cg.zone_info(zone_name, zone)
                    if not elements and len(info.get("vertex_sizes", [])) >= 2:
                        generated = _structured_boundary_elements(
                            xyz, info["vertex_sizes"]
                        )
                        self.assertTrue(generated)
                        self.assertTrue(all(len(conn) for _, conn in generated))
                        self.assertTrue(all(
                            int(conn.min()) >= 0 and int(conn.max()) < len(xyz)
                            for _, conn in generated
                        ))
                        return
        finally:
            cg.close()
        self.fail("测试 CGNS 中未找到结构化区域")


class CredentialTests(unittest.TestCase):
    def test_config_key_is_used_when_browser_key_is_empty(self):
        from cax_pipeline.api import _resolve_key

        with patch("cax_pipeline.api.read_config", return_value={"api_key": "server-key"}):
            self.assertEqual(_resolve_key(None, None), "server-key")

    def test_developer_code_uses_config_key(self):
        from cax_pipeline.api import _resolve_key

        with patch("cax_pipeline.api.read_config", return_value={"api_key": "server-key"}):
            self.assertEqual(_resolve_key("123456", None), "server-key")
            self.assertEqual(_resolve_key(None, "123456"), "server-key")

    def test_invalid_developer_code_is_rejected(self):
        from cax_pipeline.api import _resolve_key

        with self.assertRaises(HTTPException):
            _resolve_key(None, "wrong")

    def test_frontend_has_no_developer_mode_copy(self):
        static = ROOT / "cax_pipeline" / "static"
        text = (static / "index.html").read_text(encoding="utf-8") + (
            static / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("performanceDetailBtn", text)
        self.assertIn("categoryFilter", text)
        self.assertIn("performanceModal", text)
        for forbidden in ("服务端 Key", "开发者模式", "123456", "server key", "developer mode"):
            self.assertNotIn(forbidden.lower(), text.lower())


class WorkspacePickerTests(unittest.TestCase):
    def test_picker_endpoint_returns_selected_directory(self):
        from fastapi.testclient import TestClient

        from cax_pipeline.api import create_app

        with tempfile.TemporaryDirectory() as tmp:
            selected = str(Path(tmp).resolve())
            app = create_app(str(Path(tmp) / "runs"))
            with TestClient(app) as client:
                with patch("cax_pipeline.api._pick_workspace_directory", return_value=selected):
                    response = client.post("/api/workspace/pick")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"path": selected, "cancelled": False})


class ReportTests(unittest.TestCase):
    def test_report_cache_detects_source_changes_and_exports_zip(self):
        from cax_pipeline.reports import (
            build_export_zip,
            load_cached_report,
            persist_record_report,
        )
        from cax_pipeline.scanner import ScanTask

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "part.step"
            source.write_text("STEP", encoding="utf-8")
            task = ScanTask(
                id="record1",
                kind="cad",
                format="STEP",
                path=str(source),
                relative_path="part.step",
                title="part.step",
            )
            record_dir = root / "run-record"
            (record_dir / "renders").mkdir(parents=True)
            (record_dir / "renders" / "part.png").write_bytes(b"PNG")
            doc = {
                "schema_version": "2.0",
                "data_type": "cad",
                "content": {
                    "visual": {"summary": "测试零件", "profile": {"primary_class": "part"}},
                    "rendered_views": [{
                        "view": "iso",
                        "path": "renders/part.png",
                        "mime_type": "image/png",
                    }],
                },
                "meta": {"validation": {"warnings": []}},
            }
            (record_dir / "record.json").write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8"
            )
            report_json = persist_record_report(str(root), task, str(record_dir))
            cached = load_cached_report(str(root), task)
            self.assertIsNotNone(cached)
            self.assertFalse(cached["stale"])
            self.assertTrue(Path(report_json).is_file())

            source.write_text("STEP-CHANGED", encoding="utf-8")
            cached = load_cached_report(str(root), task)
            self.assertTrue(cached["stale"])

            record = {
                "id": "record1",
                "kind": "cad",
                "format": "STEP",
                "relative_path": "part.step",
                "title": "测试零件",
                "status": "ok",
                "summary": "测试零件",
                "visual_profile": {"primary_class": "part"},
                "json_path": report_json,
                "from_cache": True,
                "stale": True,
            }
            payload = build_export_zip(
                {"workspace": str(root), "created_at": "2026-10-30T00:00:00+08:00"},
                [record],
                {"record1": doc},
            )
            self.assertGreater(len(payload), 100)
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
                handle.write(payload)
                zip_path = handle.name
            try:
                import zipfile

                with zipfile.ZipFile(zip_path) as archive:
                    names = set(archive.namelist())
                self.assertIn("report.md", names)
                self.assertIn("performance.json", names)
                self.assertIn("performance.md", names)
                self.assertIn("records/record1/record.json", names)
                self.assertIn("records/record1/renders/part.png", names)
            finally:
                Path(zip_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
