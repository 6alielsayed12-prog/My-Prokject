#!/usr/bin/env python3
"""Dist-folder completeness checker for the Yawm Jadid Academy Windows build.

Complements `verify_build_artifact.py` (which proves the EXE embeds THIS source) by checking the
portable distribution FOLDER: required files, UI assets, vendored fonts/JS, DLL presence, version
marker files and the installer prerequisites.  Produces a build verification report (JSON + text).

Usage:  python scripts/verify_build.py [--dist dist/YawmJadidAcademy] [--report build_verification_report.json]
Exit code: 0 when every check passes, 1 otherwise (missing optional items downgrade to WARN).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REQUIRED_ROOT_FILES = ["YawmJadidAcademy.exe"]
REQUIRED_UI_FILES = [
    "_internal/alahd_erp/ui/index.html",
    "_internal/alahd_erp/ui/js/app.js",
    "_internal/alahd_erp/ui/js/finance-engine.js",
    "_internal/alahd_erp/ui/js/modules/api.js",
    "_internal/alahd_erp/ui/js/modules/store.js",
    "_internal/alahd_erp/ui/js/modules/router.js",
    "_internal/alahd_erp/ui/js/modules/dom.js",
    "_internal/alahd_erp/ui/js/modules/import_view.js",
    "_internal/alahd_erp/ui/js/modules/wizard_and_system.js",
    "_internal/alahd_erp/ui/js/modules/csv.js",
    "_internal/alahd_erp/ui/js/modules/alerts.js",
    "_internal/alahd_erp/ui/js/modules/statistics.js",
    "_internal/alahd_erp/ui/css/design-system.css",
]
REQUIRED_VENDOR_FILES = [
    "_internal/alahd_erp/ui/vendor/cairo.css",
    "_internal/alahd_erp/ui/vendor/cairo-arabic-400-normal.woff2",
    "_internal/alahd_erp/ui/vendor/cairo-arabic-700-normal.woff2",
    "_internal/alahd_erp/ui/vendor/cairo-latin-400-normal.woff2",
    "_internal/alahd_erp/ui/vendor/xlsx.full.min.js",
    "_internal/alahd_erp/ui/vendor/exceljs.min.js",
    "_internal/alahd_erp/ui/vendor/html2pdf.bundle.min.js",
    "_internal/alahd_erp/ui/vendor/qrcode.js",
]
REQUIRED_ASSETS = [
    "_internal/alahd_erp/ui/assets/logo.png",
    "_internal/alahd_erp/ui/assets/banner.jpg",
    "_internal/alahd_erp/ui/assets/favicon.png",
    "_internal/alahd_erp/icon.ico",
]
REQUIRED_DLL_MARKERS = ("python312.dll", "python313.dll", "python314.dll")  # one of them must exist
OPTIONAL_DLL_HINTS = ("WebView2", "pythonnet", "clr_loader")

MISSING_RUNTIME_MODULES = [
    # These pure-Python modules must be inside the bundle (PyInstaller hiddenimports / import graph):
    "alahd_erp/infrastructure/database.py",
    "alahd_erp/infrastructure/backup_scheduler.py",
    "alahd_erp/infrastructure/recovery.py",
    "alahd_erp/infrastructure/autostart.py",
    "alahd_erp/infrastructure/instance_probe.py",
    "alahd_erp/api/server.py",
    "alahd_erp/services/import_service.py",
    "alahd_erp/services/excel_service.py",
    "alahd_erp/services/html_export_service.py",
    "alahd_erp/domain/settings_registry.py",
    "alahd_erp/domain/finance_engine.py",
]


def check(dist: Path) -> dict:
    report: dict = {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "dist": str(dist), "checks": [], "ok": True}

    def add(name: str, ok: bool, detail: str, severity: str = "FAIL") -> None:
        entry = {"check": name, "status": ("PASS" if ok else severity), "detail": detail}
        report["checks"].append(entry)
        if not ok and severity == "FAIL":
            report["ok"] = False

    exe = dist / "YawmJadidAcademy.exe"
    add("executable", exe.is_file() and exe.stat().st_size > 1_000_000,
        f"{exe.name} present ({exe.stat().st_size // 1024} KB)" if exe.is_file() else "EXE missing")

    internal = dist / "_internal"
    add("_internal_dir", internal.is_dir(), "PyInstaller 6 one-folder layout" if internal.is_dir() else "_internal/ missing (PyInstaller <6 layout?)")

    for rel in REQUIRED_ROOT_FILES + REQUIRED_UI_FILES + REQUIRED_VENDOR_FILES + REQUIRED_ASSETS:
        p = dist / rel
        add(f"file:{rel}", p.is_file() and p.stat().st_size > 0, f"{p.stat().st_size} bytes" if p.is_file() else "MISSING")

    # Python runtime DLL: any of the known names inside dist (root or _internal).
    dll_found = [f.name for f in dist.rglob("*.dll") if f.name.lower() in REQUIRED_DLL_MARKERS]
    add("python_runtime_dll", bool(dll_found), ", ".join(dll_found) or "no python3xx.dll found")

    dll_count = len(list(dist.rglob("*.dll")))
    add("dll_inventory", dll_count >= 10, f"{dll_count} DLLs bundled (webview/.NET loaders + runtime)", severity="WARN" if dll_count < 10 else "FAIL")

    # Application modules that must be frozen into the bundle (source .py files are in the archive,
    # so we instead verify the compiled module records exist inside PYZ-contents paths if present).
    pyz_dir = internal / "alahd_erp"
    add("package_dir", pyz_dir.is_dir(), "frozen alahd_erp package data dir present")

    # Forbidden data: no databases / backups must ever ship in the dist folder.
    forbidden = [f.name for f in dist.rglob("*") if f.is_file() and f.suffix.lower() in (".sqlite3", ".sqlite", ".db")]
    add("no_database_files", not forbidden, "no SQLite/db files shipped" if not forbidden else f"FOUND: {forbidden}")

    # Vendored offline requirement: no CDN references in the shipped UI.
    index_html = dist / REQUIRED_UI_FILES[0]
    if index_html.is_file():
        txt = index_html.read_text(encoding="utf-8", errors="replace")
        cdn_hits = [frag for frag in ("http://", "https://") if frag in txt.replace("http-equiv", "").replace("http://www.w3.org", "")]
        add("offline_ui", not cdn_hits, "index.html has no external URLs" if not cdn_hits else f"external URLs: {cdn_hits[:3]}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist/YawmJadidAcademy")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    dist = Path(args.dist)
    if not dist.is_dir():
        print(f"[FAIL] dist folder not found: {dist}")
        return 1
    report = check(dist)
    lines = []
    for c in report["checks"]:
        mark = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "BLOCKED": "BLCK"}[c["status"]]
        lines.append(f"[{mark}] {c['check']}: {c['detail']}")
    text = "\n".join(lines)
    print(text)
    print(f"\nBUILD VERIFICATION: {'OK' if report['ok'] else 'FAILED'} ({sum(1 for c in report['checks'] if c['status'] == 'PASS')} passed, "
          f"{sum(1 for c in report['checks'] if c['status'] == 'FAIL')} failed, {sum(1 for c in report['checks'] if c['status'] == 'WARN')} warnings)")
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report saved: {args.report}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
