"""Discover parseable, unpacked CAD and CAE files in a workspace."""

from __future__ import annotations

import hashlib
import gzip
import os
from dataclasses import dataclass, field
from pathlib import Path

CAD_EXTENSIONS = (".stp", ".step", ".igs", ".iges")
CGNS_SUFFIXES = (".cgns.gz", ".cgns")
BDF_EXTENSIONS = (".bdf", ".nas", ".dat")
OP2_EXTENSIONS = (".op2", ".op4")
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".mplconfig", ".mcpdocx", ".cax_reports", "json_out", "frontend_runs",
}


@dataclass
class ScanTask:
    id: str
    kind: str
    format: str
    path: str
    relative_path: str
    title: str
    related_paths: list[str] = field(default_factory=list)
    case_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "format": self.format,
            "path": self.path,
            "relative_path": self.relative_path,
            "title": self.title,
            "related_paths": self.related_paths,
            "case_key": self.case_key,
        }


def _task_id(kind: str, path: str) -> str:
    return hashlib.sha1(f"{kind}|{path}".encode("utf-8")).hexdigest()[:16]


def _relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _is_hdf5(path: str) -> bool:
    try:
        opener = gzip.open if path.lower().endswith(".gz") else open
        with opener(path, "rb") as handle:
            return handle.read(8) == b"\x89HDF\r\n\x1a\n"
    except OSError:
        return False


def scan_workspace(root: str, max_files: int = 5000) -> tuple[list[ScanTask], dict]:
    workspace = os.path.abspath(root)
    if not os.path.isdir(workspace):
        raise NotADirectoryError(workspace)

    seen_files = 0
    skipped_dirs = 0
    skipped_links = 0
    skipped_unsupported: list[str] = []
    case_files: list[str] = []
    data_files: list[str] = []
    other_files: list[str] = []

    for current, dirs, names in os.walk(workspace, followlinks=False):
        kept_dirs: list[str] = []
        for name in sorted(dirs):
            full = os.path.join(current, name)
            if os.path.islink(full):
                skipped_links += 1
                continue
            if name.lower() in EXCLUDED_DIRS:
                skipped_dirs += 1
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(names):
            full = os.path.join(current, name)
            if os.path.islink(full):
                skipped_links += 1
                continue
            seen_files += 1
            if seen_files > max_files:
                raise ValueError(f"文件数量超过上限 {max_files}，请缩小工作区范围")
            low = name.lower()
            if low.endswith(".cas.h5"):
                case_files.append(full)
            elif low.endswith(".dat.h5"):
                data_files.append(full)
            elif low.endswith(CGNS_SUFFIXES) or Path(low).suffix in (
                CAD_EXTENSIONS + BDF_EXTENSIONS + OP2_EXTENSIONS
            ):
                other_files.append(full)

    tasks: list[ScanTask] = []
    data_by_dir: dict[str, list[str]] = {}
    for path in data_files:
        data_by_dir.setdefault(os.path.dirname(path), []).append(path)

    for path in case_files:
        folder = os.path.dirname(path)
        stem = os.path.basename(path)[: -len(".cas.h5")]
        candidates = data_by_dir.get(folder, [])
        related = [
            item for item in candidates
            if os.path.basename(item)[: -len(".dat.h5")] == stem
            or os.path.basename(item).startswith(stem + "_")
        ]
        if not related:
            related = candidates
        rel = _relative(path, workspace)
        case_key = os.path.dirname(rel).replace("\\", "/")
        tasks.append(ScanTask(
            id=_task_id("cae", path),
            kind="cae",
            format="FLUENT_CFF",
            path=path,
            relative_path=rel,
            title=stem,
            related_paths=related,
            case_key=case_key,
        ))

    for path in other_files:
        low = path.lower()
        rel = _relative(path, workspace)
        if low.endswith(CAD_EXTENSIONS):
            kind, fmt = "cad", "STEP" if low.endswith((".stp", ".step")) else "IGES"
        elif low.endswith(CGNS_SUFFIXES):
            if not _is_hdf5(path):
                skipped_unsupported.append(_relative(path, workspace))
                continue
            kind, fmt = "cae", "CGNS"
        elif Path(low).suffix in BDF_EXTENSIONS:
            kind, fmt = "cae", "NASTRAN_BDF"
        elif Path(low).suffix in OP2_EXTENSIONS:
            kind, fmt = "cae", "NASTRAN_OP2"
        else:
            continue
        tasks.append(ScanTask(
            id=_task_id(kind, path),
            kind=kind,
            format=fmt,
            path=path,
            relative_path=rel,
            title=os.path.basename(path),
        ))

    tasks.sort(key=lambda item: (item.kind, item.relative_path.lower()))
    summary = {
        "workspace": workspace,
        "files_seen": seen_files,
        "tasks": len(tasks),
        "cad": sum(item.kind == "cad" for item in tasks),
        "cae": sum(item.kind == "cae" for item in tasks),
        "skipped_dirs": skipped_dirs,
        "skipped_links": skipped_links,
        "skipped_unsupported": skipped_unsupported,
        "unmatched_data_files": max(0, len(data_files) - sum(len(item.related_paths) for item in tasks)),
    }
    return tasks, summary
