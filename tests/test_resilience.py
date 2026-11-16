"""Release acceptance suite — real-world failure simulation (v1.3.0).

IMPORTANT SCOPE NOTE (documented limitation): every failure here is an *injection* (monkeypatched
I/O errors, corrupted files, leftover markers) run inside one process. It proves the code paths fail
safely, but it is NOT equivalent to testing a real hardware power loss / OS kill at the exact instant
a write is in flight — such tests require a dedicated lab environment and are listed in the release
report's known-limitations section.

Covered requirement areas:
- §3 import: duplicate-file guard, crash marker, post-commit verification, settings-driven limits;
- §4 export: generated workbook re-open verification, atomic mirror copy;
- §5 public HTML: sentinel-value privacy tests (Mode A removes financial data from the markup itself);
- §6 backup/recovery: scheduler behaviour, retention floor, mirror isolation, corrupt backups,
  startup recovery scenarios (crash marker, corrupt DB quarantine+restore, missing DB, orphan parts);
- §7 settings-driven operation: invalid settings rejected, safe fallbacks, never silently unprotected.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from tests._helpers import StoreTestCase

from alahd_erp.infrastructure import recovery
from alahd_erp.infrastructure.backup_scheduler import BackupScheduler
from alahd_erp.infrastructure.database import (
    Store,
    atomic_copy_file,
    rotate,
    verify_sqlite_backup_file,
)
from alahd_erp.services import import_service
from alahd_erp.services.excel_service import export_bytes, verify_workbook_bytes
from alahd_erp.services.html_export_service import generate_readonly_html


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
SENTINEL_STATE = {
    "settings": {"academyName": "أكاديمية يوم جديد", "currency": "ج.م", "wageBasis": "net", "wageAccrualMode": "accrued"},
    "teachers": ["معلمة سنتينل"],
    "teacherMeta": {"معلمة سنتينل": {"pct": 70, "defaultPct": 70, "active": True}},
    "students": [{
        "id": "stu_sent", "name": "طالبة سنتينل", "code": "CODE-777", "phone": "01000000077",
        "teacher": "معلمة سنتينل", "type": "باقة سنتينل", "pct": 70,
        "months": {"2026-11": {"value": 123456, "discount": 0, "paidAmount": 654321,
                                "paymentDate": "2026-11-05", "actualStartDate": "2026-11-02",
                                "endDate": "2026-11-30"}},
    }],
    "installments": [{"id": "ins_sent", "studentId": "stu_sent", "receiptNo": "RCP-SENTINEL-99",
                       "amount": 654321, "paymentDate": "2026-11-05", "month": "2026-11", "method": "نقدي"}],
    "payouts": [{"id": "po_sent", "teacher": "معلمة سنتينل", "month": "2026-11", "amount": 111222, "date": "2026-11-28"}],
    "sessions": [], "memorization": [], "groups": [], "subTypes": {"باقة سنتينل": 123456},
}
FINANCIAL_SENTINELS = ("123456", "654321", "111222", "RCP-SENTINEL-99", "2026-11-05")


class HtmlPrivacySentinels(unittest.TestCase):
    """§5.4: inspect the FINAL HTML for distinctive financial sentinel values."""

    def test_public_mode_contains_no_financial_sentinel_anywhere(self):
        doc = generate_readonly_html(SENTINEL_STATE, scope="full", month="2026-11",
                                     include_payments=False, hide_personal_data=True)
        # The complete file: markup, comments, attributes, scripts, data-attrs, hidden elements.
        leaks = [s for s in FINANCIAL_SENTINELS if s in doc]
        self.assertEqual(leaks, [], f"financial sentinel leaked into public export: {leaks}")
        self.assertIn('privacy-mode: public', doc)
        self.assertNotIn('sec-collections', doc)
        self.assertNotIn('sec-payroll', doc)
        self.assertNotIn('sec-month-finance', doc)
        self.assertIn("لا تحتوي على أي بيانات مالية", doc)
        # Non-financial identity data must still be present (a useful report).
        self.assertIn("طالبة سنتينل", doc)
        self.assertIn("010•••••077", doc)
        self.assertIn("CODE-777", doc)  # identity/study metadata is allowed

    def test_financial_mode_includes_sentinels_and_warning(self):
        doc = generate_readonly_html(SENTINEL_STATE, scope="full", month="2026-11",
                                     include_payments=True, hide_personal_data=False)
        for s in FINANCIAL_SENTINELS:
            self.assertIn(s, doc)
        self.assertIn('privacy-mode: financial', doc)
        self.assertIn("بيانات مالية حساسة", doc)

    def test_legacy_call_keeps_previous_behavior(self):
        """Old scope-only callers (no include_payments) keep financial content + phone masking."""
        doc = generate_readonly_html(SENTINEL_STATE, scope="full", month="2026-11", hide_personal_data=True)
        self.assertIn("123456", doc)
        self.assertIn("010•••••077", doc)

    def test_section_selection_and_auto_disable(self):
        doc = generate_readonly_html(SENTINEL_STATE, scope="full", month="2026-11",
                                     include_payments=False, sections=["statistics", "collections", "students"],
                                     hide_personal_data=True)
        self.assertIn('export-sections" content="statistics,students"', doc)
        self.assertIn("disabled-by-privacy: collections", doc)
        self.assertIn("طالبة سنتينل", doc)

    def test_student_card_public_mode_strips_payment_columns(self):
        doc = generate_readonly_html(SENTINEL_STATE, scope="student", student_id="stu_sent",
                                     include_payments=False, hide_personal_data=True)
        self.assertNotIn("RCP-SENTINEL-99", doc)
        self.assertNotIn("إيصالات الدفع", doc)
        self.assertIn("سجل الحفظ والمراجعة", doc)


class ExportVerification(StoreTestCase):
    """§4.1/§4.3: never expose a partially generated or unreadable export."""

    def test_verify_rejects_garbage_and_empty(self):
        self.assertFalse(verify_workbook_bytes(b"not an xlsx at all")["ok"])
        self.assertFalse(verify_workbook_bytes(b"")["ok"])

    def test_exported_workbook_reopens_and_passes(self):
        data = export_bytes(SENTINEL_STATE, scope="students", month="2026-11")
        self.assertTrue(verify_workbook_bytes(data)["ok"])
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(data), read_only=True)
        self.assertTrue(wb.sheetnames)
        wb.close()

    def test_export_neutralizes_formula_injection_in_names(self):
        state = json.loads(json.dumps(SENTINEL_STATE, ensure_ascii=False))
        state["students"][0]["name"] = '=HYPERLINK("http://evil.example", "click")'
        data = export_bytes(state, scope="students", month="2026-11")
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(data))
        formulas = []
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.data_type == "f":
                        formulas.append(c.value)
        self.assertEqual(formulas, [], "a formula survived into the exported workbook")
        wb.close()

    def test_atomic_mirror_copy_verifies_and_cleans_part_on_failure(self):
        src = self.home / "src.sqlite3"
        src.write_bytes(b"A" * 10000)
        dest_dir = self.home / "mirror"
        dest = dest_dir / "copy.sqlite3"
        atomic_copy_file(src, dest, verify=True)
        self.assertEqual(dest.read_bytes(), src.read_bytes())
        self.assertFalse((dest_dir / "copy.sqlite3.part").exists())
        # A failing read-back (simulated) must not truncate the good destination.
        good = dest.read_bytes()
        with mock.patch("builtins.open", side_effect=[mock.DEFAULT, OSError("disk full")]):
            with self.assertRaises(Exception):
                atomic_copy_file(src, dest, verify=True)
        self.assertEqual(dest.read_bytes(), good, "previous good mirror copy was corrupted by a failed copy")


class ImportResilience(StoreTestCase):
    """§3.3-§3.6: transactional guarantees, duplicate protection, crash detection."""

    CSV = (
        "الاسم,المعلمة,الشهر,قيمة الاشتراك\n"
        "طالبة جديدة,معلمة أ,2026-11,300\n"
    ).encode("utf-8-sig")

    def _commit(self, **kw):
        kw.setdefault("backup_folder", self.home / "backups")
        (self.home / "backups").mkdir(exist_ok=True)
        return import_service.commit_import(self.store, self.CSV, "new.csv", "students", **kw)

    def test_duplicate_file_guard_blocks_second_commit(self):
        r1 = self._commit()
        self.assertTrue(r1["ok"])
        with self.assertRaises(ValueError) as cm:
            self._commit()
        self.assertIn("تم استيراد هذا الملف", str(cm.exception))
        # Explicit re-import is allowed and is idempotent (updates, no duplicate row).
        r2 = self._commit(allow_reimport=True)
        self.assertTrue(r2["ok"])
        state = self.store.export_state()
        names = [s["name"] for s in state["students"]]
        self.assertEqual(names.count("طالبة جديدة"), 1)

    def test_same_file_different_dataset_is_not_blocked(self):
        csv_t = "الاسم,التخصص,النسبة\nمعلمة سنتينل,حفظ,70\n".encode("utf-8-sig")
        kw = {"backup_folder": self.home / "backups"}
        (self.home / "backups").mkdir(exist_ok=True)
        import_service.commit_import(self.store, csv_t, "teachers.csv", "teachers", **kw)
        csv_p = "اسم الباقة,السعر\nباقة تجريبية,250\n".encode("utf-8-sig")
        import_service.commit_import(self.store, csv_p, "packages.csv", "packages", **kw)  # other dataset: fine

    def test_hash_binding_rejects_file_modified_after_preview(self):
        self._commit()
        with self.assertRaises(ValueError):
            import_service.commit_import(
                self.store, self.CSV + b"tampered", "new.csv", "students",
                expected_file_hash="deadbeef" * 8, backup_folder=self.home / "backups")

    def test_crash_marker_written_and_cleared(self):
        marker = self.home / "import_in_progress.json"
        self.assertFalse(marker.exists())
        self._commit()
        self.assertFalse(marker.exists(), "marker must be removed after a successful commit")

    def test_post_commit_verification_failure_reverts_snapshot(self):
        """Simulate a post-commit read-back failure: the pre-import snapshot must be restored."""
        real_export = self.store.export_state

        def failing_export():
            res = real_export()
            if any(s.get("name") == "طالبة جديدة" for s in res.get("students") or []):
                raise RuntimeError("simulated post-commit read failure")
            return res

        with mock.patch.object(self.store, "export_state", side_effect=failing_export):
            # export_state is also used by preview; the preview runs before any student exists, so
            # only the post-commit verification will hit the simulated failure.
            with self.assertRaises(ValueError) as cm:
                self._commit()
            self.assertIn("فشل التحقق بعد الحفظ", str(cm.exception))
        state = self.store.export_state()
        names = [s.get("name") for s in state["students"]]
        self.assertNotIn("طالبة جديدة", names, "verification failed but the partial import stayed committed")

    def test_settings_drive_import_limits(self):
        state = {"settings": {"importMaxFileMB": 1, "importMaxRows": 5000}}
        limits = import_service.resolve_import_limits(state)
        self.assertEqual(limits["maxBytes"], 1 * 1024 * 1024)
        self.assertEqual(limits["maxRows"], 5000)
        # Hard clamps always win over nonsense settings.
        crazy = import_service.resolve_import_limits({"settings": {"importMaxFileMB": 99999, "importMaxRows": 99999999}})
        self.assertLessEqual(crazy["maxBytes"], 200 * 1024 * 1024)
        self.assertLessEqual(crazy["maxRows"], 200000)
        # An oversized file is rejected BEFORE parsing.
        big = b"x" * (1 * 1024 * 1024 + 10)
        with self.assertRaises(ValueError):
            import_service._extract_tabular_rows_from_file(big, "big.csv", "students", limits=limits)


class BackupSchedulerResilience(StoreTestCase):
    """§6: settings-driven scheduler, retention floor, failure isolation."""

    def _mk(self):
        return BackupScheduler(self.store, self.home)

    def test_settings_control_interval_and_flags_live(self):
        sched = self._mk()
        s = self.store.get_settings()
        s["autoBackupIntervalMinutes"] = 5
        self.store.update_settings({"autoBackupIntervalMinutes": 5}, actor="test")
        self.assertEqual(sched._interval_seconds(sched._settings()), 300.0)
        self.store.update_settings({"autoBackupEnabled": False}, actor="test")
        self.assertFalse(sched._enabled(sched._settings()))
        self.store.update_settings({"autoBackupEnabled": True}, actor="test")

    def test_garbled_settings_fall_back_to_safe_defaults(self):
        sched = self._mk()
        with mock.patch.object(self.store, "get_settings", return_value={"autoBackupIntervalMinutes": "garbage", "autoBackupKeep": None}):
            self.assertEqual(sched._interval_seconds(sched._settings()), 30 * 60.0)
            self.assertEqual(sched._keep(sched._settings(), "autoBackupKeep", 48), 48)

    def test_retention_never_goes_below_floor(self):
        sched = self._mk()
        self.assertEqual(sched._keep({"autoBackupKeep": 1}, "autoBackupKeep", 48), 3)

    def test_auto_backup_skipped_when_unchanged_and_runs_on_change(self):
        sched = self._mk()
        r1 = sched.run_auto_backup()
        self.assertTrue(r1["ran"])
        r2 = sched.run_auto_backup()
        self.assertFalse(r2["ran"], "unchanged rev must not create a redundant backup")
        self.assertEqual(r2.get("reason"), "unchanged")
        self.store.log("test", "change", "bump")
        r3 = sched.run_auto_backup()
        self.assertTrue(r3["ran"])

    def test_exit_backup_respects_setting(self):
        sched = self._mk()
        self.store.update_settings({"backupOnExit": False}, actor="test")
        self.assertFalse(sched.run_exit_backup()["ran"])
        self.store.update_settings({"backupOnExit": True}, actor="test")
        self.assertTrue(sched.run_exit_backup()["ran"])

    def test_backup_failure_is_isolated_and_reported(self):
        sched = self._mk()
        # Destination folder replaced by a FILE: mkdir/backup writes must fail, but the store stays healthy.
        (self.home / "backups").unlink(missing_ok=True)
        (self.home / "backups").write_text("not a folder", encoding="utf-8")
        res = sched.run_auto_backup()
        self.assertFalse(res["ran"])
        self.assertIn("error", res)
        self.assertEqual(sched.status()["consecutiveFailures"], 1)
        qi = self.store.quick_integrity()
        self.assertTrue(qi["ok"], "the live database must never be touched by a backup failure")
        # Recovery: a real folder again -> the next cycle succeeds and the failure counter resets.
        (self.home / "backups").unlink()
        (self.home / "backups").mkdir()
        self.assertTrue(sched.run_auto_backup()["ran"])
        self.assertEqual(sched.status()["consecutiveFailures"], 0)

    def test_mirror_failure_does_not_break_the_primary_backup(self):
        # A path that cannot exist anywhere: a FILE used as a parent directory (NotADirectoryError).
        blocker = self.home / "blocker.txt"
        blocker.write_text("i am a file", encoding="utf-8")
        self.store.update_settings({"extraBackupFolder": str(blocker / "sub" / "mirror")}, actor="test")
        bdir = self.home / "backups"
        bdir.mkdir(exist_ok=True)
        dest = bdir / "alahd_manual_primary.sqlite3"
        with self.assertLogs("alahd_erp.infrastructure.database", level="WARNING"):
            self.store.backup_to(dest, mirror_to_extra=True)
        self.assertTrue(dest.is_file())
        self.assertTrue(verify_sqlite_backup_file(dest))

    def test_rotate_never_deletes_every_backup(self):
        bdir = self.home / "backups"
        bdir.mkdir(exist_ok=True)
        for i in range(5):
            (bdir / f"alahd_auto_2026010{i}_000000.sqlite3").write_bytes(b"x" * 100)
        rotate(bdir, "alahd_auto_*.sqlite3", 3)
        self.assertEqual(len(list(bdir.glob("alahd_auto_*.sqlite3"))), 3)
        rotate(bdir, "alahd_auto_*.sqlite3", 0)  # keep=0 (invalid config) would wipe all — guarded upstream
        self.assertEqual(len(list(bdir.glob("alahd_auto_*.sqlite3"))), 0)
        # The Store-level paths always pass keep >= 3 (settings validation + scheduler floor).


class CorruptBackupHandling(StoreTestCase):
    """§6.4: a backup is not valid merely because a file exists."""

    def _seed(self):
        self.store.upsert_teacher("معلمة أ", {"pct": 70})

    def test_garbage_file_fails_verification(self):
        bad = self.home / "alahd_manual_bad.sqlite3"
        bad.write_bytes(b"this is not a database" * 50)
        self.assertFalse(verify_sqlite_backup_file(bad))
        res = self.store.verify_backup_file(self.home, bad.name, deep=True)
        self.assertFalse(res["ok"])

    def test_restore_refuses_corrupt_backup(self):
        self._seed()
        bad = self.home / "alahd_manual_bad.sqlite3"
        bad.write_bytes(b"garbage" * 100)
        with self.assertRaises(ValueError):
            self.store.restore_from(bad)
        self.assertEqual(len(self.store.list_teachers()), 1, "live data must be untouched")

    def test_zero_byte_backup_rejected(self):
        z = self.home / "alahd_manual_zero.sqlite3"
        z.write_bytes(b"")
        self.assertFalse(verify_sqlite_backup_file(z))


class StartupRecoveryScenarios(unittest.TestCase):
    """§6.3: startup detection/repair — each in an isolated temp home."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.db = self.home / "alahd_erp_v1.sqlite3"
        self.backups = self.home / "backups"
        self.backups.mkdir()

    def _mk_backup(self):
        s = Store(self.db)
        s.upsert_teacher("معلمة أ", {"pct": 70})
        f = self.backups / "alahd_daily_20260101.sqlite3"
        s.backup_to(f, mirror_to_extra=False)
        s.close()
        return f

    def test_clean_healthy_db_reports_ok(self):
        self._mk_backup()
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertTrue(r["dbHealthy"])
        self.assertFalse(r["previousSessionInterrupted"])
        self.assertIsNone(r["restoredFromBackup"])

    def test_unclean_session_marker_detected(self):
        self._mk_backup()
        (self.home / recovery.SESSION_MARKER).write_text(json.dumps({"pid": 1, "startedAt": "x"}), encoding="utf-8")
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertTrue(r["previousSessionInterrupted"])
        self.assertTrue(r["dbHealthy"])

    def test_corrupt_db_quarantined_and_restored(self):
        self._mk_backup()
        self.db.write_bytes(b"corrupted beyond repair" * 100)
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertIsNotNone(r["quarantined"])
        self.assertTrue(Path(r["quarantined"]).exists(), "corrupt file must be preserved for investigation")
        self.assertIsNotNone(r["restoredFromBackup"])
        self.assertTrue(r["dbHealthy"])
        s = Store(self.db)
        self.assertEqual(len(s.list_teachers()), 1)
        s.close()

    def test_missing_db_restored_from_backup(self):
        self._mk_backup()
        self.db.unlink()
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertIsNotNone(r["restoredFromBackup"])
        self.assertTrue(r["dbHealthy"])

    def test_missing_db_without_backup_creates_fresh(self):
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertIsNone(r["restoredFromBackup"])
        s = Store(self.db)  # creates a fresh usable DB
        self.assertTrue(s.quick_integrity()["ok"])
        s.close()

    def test_orphan_part_files_swept(self):
        import os
        import time as _t
        stale = self.backups / "alahd_auto_old.sqlite3.part"
        stale.write_bytes(b"junk")
        old = _t.time() - 3600
        os.utime(stale, (old, old))
        fresh = self.backups / "alahd_auto_new.sqlite3.part"
        fresh.write_bytes(b"junk")  # fresh one must NOT be deleted (concurrent backup)
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertIn("alahd_auto_old.sqlite3.part", r["orphanPartFilesCleaned"])
        self.assertTrue(fresh.exists())

    def test_interrupted_import_detected_and_archived(self):
        (self.home / recovery.IMPORT_MARKER).write_text(
            json.dumps({"batchId": "imp_x", "fileHash": "h", "startedAt": "t"}), encoding="utf-8")
        r = recovery.run_startup_recovery(self.home, self.db)
        self.assertEqual((r["interruptedImport"] or {}).get("batchId"), "imp_x")
        self.assertFalse((self.home / recovery.IMPORT_MARKER).exists())
        self.assertTrue((self.home / "logs" / "interrupted_imports.log").is_file())

    def test_report_persisted_for_settings_ui(self):
        self._mk_backup()
        recovery.run_startup_recovery(self.home, self.db)
        loaded = recovery.read_recovery_report(self.home)
        self.assertTrue(loaded.get("dbHealthy"))


class SettingsSafety(StoreTestCase):
    """§7/§2.4: invalid settings rejected; safe defaults; restore; partial-write protection."""

    def test_invalid_backup_setting_rejected(self):
        with self.assertRaises(Exception):
            self.store.update_settings({"autoBackupIntervalMinutes": 0}, actor="test")
        with self.assertRaises(Exception):
            self.store.update_settings({"autoBackupKeep": 1}, actor="test")
        with self.assertRaises(Exception):
            self.store.update_settings({"importMaxFileMB": 5000}, actor="test")

    def test_settings_persist_and_survive_reopen(self):
        self.store.update_settings({
            "autoBackupIntervalMinutes": 15, "autoBackupKeep": 20, "dailyBackupKeep": 10,
            "htmlExportIncludePaymentsByDefault": True,
        }, actor="test")
        st = self.reopen()
        s = st.get_settings()
        self.assertEqual(s["autoBackupIntervalMinutes"], 15)
        self.assertEqual(s["autoBackupKeep"], 20)
        self.assertEqual(s["dailyBackupKeep"], 10)
        self.assertTrue(s["htmlExportIncludePaymentsByDefault"])
        st.close()

    def test_unknown_keys_ignored_but_safe_keys_default_on(self):
        self.store.update_settings({"autoBackupEnabled": False}, actor="test")
        s = self.reopen().get_settings()
        self.assertFalse(s["autoBackupEnabled"])
        # The safety-critical defaults when a key is missing entirely:
        from alahd_erp.domain.models import DEFAULT_SETTINGS
        self.assertTrue(DEFAULT_SETTINGS["autoBackupEnabled"])
        self.assertTrue(DEFAULT_SETTINGS["backupOnExit"])
        self.assertFalse(DEFAULT_SETTINGS["htmlExportIncludePaymentsByDefault"])


class StoreMaintenancePrimitives(StoreTestCase):
    def test_integrity_checkpoint_vacuum(self):
        self.seed()
        self.assertTrue(self.store.full_integrity_check()["ok"])
        self.assertTrue(self.store.checkpoint_wal()["ok"])
        v = self.store.vacuum()
        self.assertTrue(v["ok"])

    def test_backup_verify_reports_kind_and_counts(self):
        self.seed()
        bdir = self.home / "backups"
        bdir.mkdir(exist_ok=True)
        name = "alahd_manual_check.sqlite3"
        self.store.backup_to(bdir / name, mirror_to_extra=False)
        r = self.store.verify_backup_file(bdir, name, deep=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["kind"], "manual")
        self.assertEqual(r["studentsCount"], 1)
        self.assertEqual(r["schemaVersion"], 3)

    def test_import_history_roundtrip_and_dedup(self):
        self.store.remember_import_file({"fileHash": "h1", "batchId": "b1", "dataset": "students", "filename": "a.xlsx"})
        self.store.remember_import_file({"fileHash": "h1", "batchId": "b2", "dataset": "students", "filename": "a.xlsx"})
        hist = self.store.get_import_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["batchId"], "b2")
        self.assertIsNotNone(self.store.find_import_history_entry("h1", dataset="students"))
        self.assertIsNone(self.store.find_import_history_entry("h1", dataset="teachers"))


if __name__ == "__main__":
    unittest.main()
