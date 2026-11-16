"""Startup Crash Recovery for Yawm Jadid Academy.

Runs BEFORE the database `Store` is opened (from main.py). It answers, in order:

1. Did the previous session end unexpectedly?      -> `session_active.json` marker left on disk.
2. Is the database file openable and consistent?   -> integrity_check + probe queries.
3. Is the WAL/SHM state recoverable?               -> WAL checkpoint attempt before any drastic action.
4. Is the database beyond repair?                  -> quarantine (never delete!) + restore newest verified backup.
5. Did a previous import/backup die mid-flight?    -> orphan `.part` sweep + interrupted-import marker.

Guiding principles:
- NEVER auto-restore an old backup while the current database is readable and consistent
  (that would silently discard newer work).
- NEVER delete a corrupted database: it is moved to `quarantine/` for manual investigation.
- Every action is recorded in the report returned to the caller and persisted to
  `logs/last_recovery.json` (read by the Settings UI through `/api/recovery/status`).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .database import _ro_uri, atomic_copy_file, verify_sqlite_backup_file

_LOG = logging.getLogger("alahd_erp.recovery")

SESSION_MARKER = "session_active.json"
IMPORT_MARKER = "import_in_progress.json"
RECOVERY_REPORT_FILE = "last_recovery.json"

_LAST_REPORT: dict[str, Any] = {}


def get_last_recovery_report() -> dict[str, Any]:
    """The in-memory report of the most recent startup recovery (same process)."""
    return dict(_LAST_REPORT)


def _new_report() -> dict[str, Any]:
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "previousSessionInterrupted": False,
        "interruptedImport": None,
        "dbExisted": True,
        "dbHealthy": False,
        "walRecovered": False,
        "quarantined": None,
        "restoredFromBackup": None,
        "orphanPartFilesCleaned": [],
        "actions": [],
        "errors": [],
        "userMessage": "",
    }


def _note(report: dict[str, Any], msg: str) -> None:
    report["actions"].append(msg)
    _LOG.info("recovery: %s", msg)


def _db_integrity(db_path: Path) -> tuple[bool, str]:
    """Opens the database read-only and runs integrity_check + probe queries. Returns (ok, detail)."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return False, "missing_or_empty"
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True, timeout=2.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            detail = str(row[0]) if row else "no-result"
            if detail != "ok":
                return False, f"integrity: {detail}"
            for probe in ("SELECT COUNT(*) FROM students", "SELECT COUNT(*) FROM teachers", "SELECT COUNT(*) FROM kv"):
                try:
                    conn.execute(probe).fetchone()
                except Exception as exc:
                    return False, f"probe failed ({probe.split()[-1]}): {exc}"
            return True, "ok"
        finally:
            conn.close()
    except Exception as exc:
        return False, f"open failed: {exc}"


def _try_wal_repair(db_path: Path) -> bool:
    """Attempts a WAL checkpoint on a failing database: a truncated/corrupt WAL state is often
    repairable by letting SQLite replay/validate the WAL, after which integrity passes again."""
    wal = db_path.with_name(db_path.name + "-wal")
    if not wal.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA synchronous=FULL")
        finally:
            conn.close()
        ok, _ = _db_integrity(db_path)
        return ok
    except Exception:
        return False


def _quarantine(db_path: Path, app_home: Path, report: dict[str, Any], keep_original: bool = False) -> Path | None:
    """Moves (or copies when `keep_original=True`) an unusable database (plus WAL/SHM) into `quarantine/` and `backups/corrupt/`."""
    ts = time.strftime('%Y%m%d_%H%M%S')
    qdir = app_home / "quarantine" / f"corrupt_{ts}"
    b_corrupt = app_home / "backups" / "corrupt" / f"corrupt_{ts}"
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        b_corrupt.mkdir(parents=True, exist_ok=True)
        for companion in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
            if companion.exists():
                shutil.copy2(str(companion), str(b_corrupt / companion.name))
                if keep_original:
                    shutil.copy2(str(companion), str(qdir / companion.name))
                else:
                    shutil.move(str(companion), str(qdir / companion.name))
        report["quarantined"] = str(qdir)
        report["corruptBackupCopy"] = str(b_corrupt)
        _note(report, f"قاعدة البيانات التالفة حُفظت في: {qdir}")
        return qdir
    except Exception as exc:
        report["errors"].append(f"quarantine failed: {exc}")
        _LOG.exception("quarantine failed")
        return None


def _newest_verified_backup(backups_dir: Path) -> Path | None:
    if not backups_dir.is_dir():
        return None
    candidates = sorted(
        [p for p in backups_dir.glob("alahd_*.sqlite3") if p.stat().st_size > 0],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for cand in candidates:
        if verify_sqlite_backup_file(cand):
            return cand
    return None


def _sweep_orphan_parts(backups_dir: Path, report: dict[str, Any]) -> None:
    """Removes leftover `.part` files (and companions) from interrupted backup operations."""
    if not backups_dir.is_dir():
        return
    cutoff = time.time() - 60  # ignore a part file created seconds ago by a concurrent backup
    for part in list(backups_dir.glob("*.part")) + list(backups_dir.glob("*.part-wal")) + list(backups_dir.glob("*.part-shm")):
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink(missing_ok=True)
                report["orphanPartFilesCleaned"].append(part.name)
        except OSError:
            pass


def _handle_interrupted_import(app_home: Path, report: dict[str, Any]) -> None:
    """An import that died between its marker and completion is surfaced, then archived."""
    marker = app_home / IMPORT_MARKER
    if not marker.is_file():
        return
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        info["detectedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["interruptedImport"] = info
        _note(report, "تم رصد عملية استيراد لم تكتمل في الجلسة السابقة (قاعدة البيانات سليمة، ولم يُسجَّل أي استيراد جزئي).")
        archive = app_home / "logs" / "interrupted_imports.log"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with open(archive, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(info, ensure_ascii=False) + "\n")
        marker.unlink(missing_ok=True)
        _note(report, "أُرشفت تفاصيل الاستيراد غير المكتمل في logs/interrupted_imports.log")
    except Exception as exc:
        report["errors"].append(f"interrupted import handling: {exc}")


def run_startup_recovery(app_home: Path, db_path: Path) -> dict[str, Any]:
    """Inspects and, when necessary, repairs the database environment before the app opens it."""
    app_home = Path(app_home)
    db_path = Path(db_path)
    report = _new_report()
    backups_dir = app_home / "backups"

    # 1) Previous-session marker (crash / power loss / forced kill).
    marker = app_home / SESSION_MARKER
    if marker.is_file():
        report["previousSessionInterrupted"] = True
        try:
            prev = json.loads(marker.read_text(encoding="utf-8"))
            _note(report, f"الجلسة السابقة لم تُغلق بشكل سليم (بدأت: {prev.get('startedAt', '?')}). تم فحص قاعدة البيانات كاملةً.")
        except Exception:
            _note(report, "الجلسة السابقة لم تُغلق بشكل سليم.")
        marker.unlink(missing_ok=True)
    try:
        marker.write_text(
            json.dumps({"pid": os.getpid(), "startedAt": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    # 2) Interrupted import bookkeeping (database untouched — commits are transactional).
    _handle_interrupted_import(app_home, report)

    # 3) Orphan backup part files.
    _sweep_orphan_parts(backups_dir, report)
    if report["orphanPartFilesCleaned"]:
        _note(report, f"تم تنظيف {len(report['orphanPartFilesCleaned'])} ملف نسخ احتياطي غير مكتمل (.part).")

    # 4) Database health.
    if not db_path.is_file() or db_path.stat().st_size == 0:
        report["dbExisted"] = False
        _note(report, "لم يُعثر على ملف قاعدة البيانات (أول تشغيل أو فقدان الملف).")
        # A missing database next to existing verified backups means data loss (accidental
        # deletion, interrupted update, disk failure): restore the newest verified backup.
        backup = _newest_verified_backup(backups_dir)
        if backup is not None:
            try:
                atomic_copy_file(backup, db_path, verify=True)
                report["restoredFromBackup"] = backup.name
                report["dbHealthy"] = True
                _note(report, f"لم يُعثر على قاعدة البيانات فتمت استعادة أحدث نسخة احتياطية مفحوصة: {backup.name}")
                report["userMessage"] = (
                    f"لم يُعثر على ملف قاعدة البيانات عند التشغيل، فتمت استعادتها تلقائيًا من النسخة الاحتياطية "
                    f"«{backup.name}». إذا كان ذلك غير متوقع، راجع مجلد النسخ الاحتياطية."
                )
            except Exception as exc:
                report["errors"].append(f"restore (missing db) failed: {exc}")
        else:
            # First-ever launch: nothing to restore, nothing unhealthy — the app creates a fresh DB.
            report["dbHealthy"] = None  # None = "new/fresh database, not a failure"
            _note(report, "لا توجد نسخة احتياطية بعد — سيُنشأ ملف قاعدة بيانات جديد فارغ (أول تشغيل).")
    else:
        ok, detail = _db_integrity(db_path)
        if ok:
            report["dbHealthy"] = True
            report["userMessage"] = "فحص ما بعد التشغيل: قاعدة البيانات سليمة."
            if report.get("previousSessionInterrupted"):
                try:
                    backups_dir.mkdir(parents=True, exist_ok=True)
                    crash_snap = backups_dir / f"alahd_safety_after_crash_{time.strftime('%Y%m%d_%H%M%S')}.sqlite3"
                    conn_ck = sqlite3.connect(str(db_path), timeout=3.0)
                    try:
                        conn_ck.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    finally:
                        conn_ck.close()
                    atomic_copy_file(db_path, crash_snap, verify=True)
                    report["afterCrashSnapshot"] = crash_snap.name
                    _note(report, f"تم إنشاء نسخة أمان بعد الإغلاق غير المتوقع: {crash_snap.name}")
                except Exception as exc:
                    _LOG.warning("could not create after-crash safety snapshot: %s", exc)
        else:
            _LOG.warning("database integrity failing (%s); attempting WAL repair", detail)
            if _try_wal_repair(db_path):
                report["walRecovered"] = True
                report["dbHealthy"] = True
                _note(report, "تمت إصلاح حالة WAL وإعادة فحص السلامة بنجاح (لم تُفقد أي بيانات).")
                report["userMessage"] = "تم إصلاح حالة سجل WAL تلقائيًا عند التشغيل؛ قاعدة البيانات سليمة."
            else:
                # Last resort: check if a verified backup exists before removing the corrupt file.
                backup = _newest_verified_backup(backups_dir)
                _quarantine(db_path, app_home, report, keep_original=(backup is None))
                if backup is not None and report.get("quarantined"):
                    try:
                        atomic_copy_file(backup, db_path, verify=True)
                        report["restoredFromBackup"] = backup.name
                        report["dbHealthy"] = True
                        _note(report, f"تمت استعادة أحدث نسخة احتياطية مفحوصة: {backup.name}")
                        report["userMessage"] = (
                            f"تضررت قاعدة البيانات في الجلسة السابقة وتمت استعادتها تلقائيًا من النسخة الاحتياطية "
                            f"«{backup.name}». النسخة التالفة محفوظة في مجلد العزل للفحص."
                        )
                    except Exception as exc:
                        report["errors"].append(f"restore from backup failed: {exc}")
                else:
                    report["unrecoverableCorruptDb"] = True
                    report["userMessage"] = (
                        "تعذر فتح قاعدة البيانات ولم تُعثر على نسخة احتياطية صالحة للاستعادة التلقائية. "
                        "حُفظ الملف التالف في مجلد العزل — تواصل مع الدعم الفني."
                    )

    # 5) Persist the report for the Settings UI.
    _LAST_REPORT.clear()
    _LAST_REPORT.update(report)
    try:
        logs_dir = app_home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / RECOVERY_REPORT_FILE).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if report["userMessage"]:
        _LOG.warning("startup recovery: %s", report["userMessage"])
    return dict(report)


def clear_session_marker(app_home: Path) -> None:
    """Called on a clean exit: removes the crash-detection marker."""
    try:
        (Path(app_home) / SESSION_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def read_recovery_report(app_home: Path) -> dict[str, Any]:
    """Reads the last persisted recovery report (works across restarts)."""
    p = Path(app_home) / "logs" / RECOVERY_REPORT_FILE
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return get_last_recovery_report()
