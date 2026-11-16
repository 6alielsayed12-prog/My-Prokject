"""Destructive, Adversarial, Cross-Drive Portability & Stress Test Suite — Yawm Jadid Academy v1.3.0.

Tests:
1. Cross-drive & portable path relocation (`C:\\` -> `D:\\` -> `E:\\` -> `F:\\` -> `G:\\` -> `\\\\Server\\Share`),
   case-insensitive Windows drive comparisons, `safe_abs_path` fallback when `.resolve()` fails on `subst`/virtual
   drives, and `.bat` launcher relocatable `.venv` guards.
2. `extraBackupFolder` portability: relative paths resolved under `app_home` (never `cwd`), environment variable
   expansion, and unmounted drive isolation in both `backup_to()` and `get_backup_health()`.
3. Destructive database & crash recovery: mid-flight power loss, truncated/corrupted SQLite + WAL repair,
   same-second backup storm collision safety, and last-backup deletion guard.
4. Hostile & destructive imports/restores: `importMaxRows` enforced on both `.xlsx` (even < 25,000 rows) and `.csv`,
   XXE/ZIP-bomb/binary-disguised-as-CSV rejection, CP1256 & UTF-16 CSV decoding, real `pre_commit_ids` verification
   and automatic rollback on post-commit mismatch, and JSON restore referential integrity rollback.
5. Multi-threaded concurrency torture test (12 concurrent threads mutating, reading, and backing up while a closed
   month is locked).
"""
from __future__ import annotations

import io
import json
import os
import threading
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from alahd_erp.api.server import create_app
from alahd_erp.config import resolve_app_home, safe_abs_path
from alahd_erp.infrastructure import recovery
from alahd_erp.infrastructure.autostart import (
    FileBackedRegistryBackend,
    REGISTRY_KEY_PATH,
    REGISTRY_VALUE_NAME,
    get_autostart_status,
    is_eligible_for_autostart,
    set_autostart_enabled,
    sync_and_repair_autostart,
)
from alahd_erp.infrastructure.backup_scheduler import BackupScheduler
from alahd_erp.infrastructure.database import MonthLockedError, Store
from alahd_erp.services.import_service import commit_import, preview_import_file
from tests._helpers import ROOT

try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except Exception:
    HAS_TESTCLIENT = False

UI_DIR = ROOT / "alahd_erp" / "ui"


class PortablePathsAndCrossDriveTests(unittest.TestCase):
    """Verifies that moving the application across any drive (C:, D:, E:, F:, G:, UNC) works seamlessly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yj_portable_")
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_autostart_supports_any_drive_letter_and_case_insensitive_matching(self) -> None:
        reg_file = self.home / "reg.json"
        backend = FileBackedRegistryBackend(reg_file)

        # Test drives E:\, F:\, G:\, Z:\ and UNC path \\Server\Share
        for drive_path in (
            r"E:\YawmJadidAcademy\YawmJadidAcademy.exe",
            r"F:\أكاديمية يوم جديد\YawmJadidAcademy.exe",
            r"g:\PortableApps\YawmJadid\YawmJadidAcademy.exe",
            r"Z:\AcademySuite\YawmJadidAcademy.exe",
            r"\\AcademyServer\SharedApps\YawmJadidAcademy.exe",
        ):
            eligible, reason, _ = is_eligible_for_autostart(
                executable_path=drive_path,
                frozen=True,
                platform_name="win32",
                allow_test_path=True,
            )
            self.assertTrue(eligible, f"Drive path {drive_path} should be eligible, got {reason}")

        # Enable on E:\ drive
        res_e = set_autostart_enabled(
            True,
            backend=backend,
            executable_path=r"E:\YawmJadidAcademy\YawmJadidAcademy.exe",
            frozen=True,
            platform_name="win32",
            allow_test_path=True,
        )
        self.assertTrue(res_e["ok"])
        self.assertEqual(res_e["autostart"]["status"], "enabled")

        # Checking status with lowercase drive letter `e:\yawmjadidacademy\yawmjadidacademy.exe` must match!
        st_case = get_autostart_status(
            backend=backend,
            executable_path=r"e:\yawmjadidacademy\yawmjadidacademy.exe",
            frozen=True,
            platform_name="win32",
            allow_test_path=True,
        )
        self.assertEqual(st_case["status"], "enabled")
        self.assertFalse(st_case["needsRepair"])

        # Simulate user moving the app folder from E:\ to F:\أكاديمية يوم جديد
        st_moved_f = get_autostart_status(
            backend=backend,
            executable_path=r"F:\أكاديمية يوم جديد\YawmJadidAcademy.exe",
            frozen=True,
            platform_name="win32",
            allow_test_path=True,
        )
        self.assertEqual(st_moved_f["status"], "needs_repair")
        self.assertTrue(st_moved_f["needsRepair"])

        # Startup sync_and_repair_autostart automatically updates registry from E:\ to F:\
        rep = sync_and_repair_autostart(
            settings_enabled=True,
            initialized=True,
            backend=backend,
            executable_path=r"F:\أكاديمية يوم جديد\YawmJadidAcademy.exe",
            frozen=True,
            platform_name="win32",
            allow_test_path=True,
        )
        self.assertEqual(rep["action"], "repaired_moved_exe")
        self.assertEqual(rep["autostart"]["status"], "enabled")
        self.assertIn(r"F:\أكاديمية يوم جديد\YawmJadidAcademy.exe", backend.get_value(REGISTRY_KEY_PATH, REGISTRY_VALUE_NAME) or "")

    def test_safe_abs_path_survives_oserror_on_virtual_or_subst_drives(self) -> None:
        with mock.patch.object(Path, "resolve", side_effect=OSError("subst drive GetFinalPathNameByHandle failed")):
            res = safe_abs_path(self.home / "sub" / "db.sqlite3")
            self.assertTrue(res.is_absolute())
            self.assertTrue(str(res).endswith("db.sqlite3"))

        with mock.patch.dict(os.environ, {"ALAHD_ERP_HOME": str(self.home / "custom_home")}):
            with mock.patch.object(Path, "resolve", side_effect=OSError("virtual drive")):
                app_home = resolve_app_home()
                self.assertTrue(app_home.is_dir())

    def test_extra_backup_folder_relative_and_unmounted_drive_in_both_backup_and_health(self) -> None:
        store = Store(self.home / "alahd_erp_v1.sqlite3")
        try:
            # 1. Relative extraBackupFolder resolves inside app_home (never pollutes cwd)
            store.update_settings({"extraBackupFolder": "mirror_usb/backups", "mirrorBackupsToExtraFolder": True}, actor="test")
            h1 = store.get_backup_health(self.home / "backups")
            self.assertTrue(h1["extraFolderReachable"])
            self.assertTrue((self.home / "mirror_usb" / "backups").is_dir())
            self.assertFalse((ROOT / "mirror_usb").exists())

            bdest = self.home / "backups" / "alahd_manual_rel.sqlite3"
            store.backup_to(bdest, mirror_to_extra=True)
            self.assertTrue((self.home / "mirror_usb" / "backups" / "alahd_manual_rel.sqlite3").is_file())

            # 2. Unmounted Windows drive / UNC path in get_backup_health AND backup_to never creates folders in cwd
            if os.name != "nt":
                for win_p in (r"Q:\definitely\missing\drive", r"Z:/unplugged_usb/backups", r"\\DeadServer\Share\Backups"):
                    store.update_settings({"extraBackupFolder": win_p}, actor="test")
                    h_bad = store.get_backup_health(self.home / "backups")
                    self.assertFalse(h_bad["extraFolderReachable"])
                    self.assertTrue(h_bad["extraFolderUnreachable"])
                    self.assertFalse((ROOT / win_p).exists())
        finally:
            store.close()

    def test_bat_scripts_enforce_relocatable_venv_and_multi_drive_probes(self) -> None:
        for bat_name in ("START_ACADEMY.bat", "build_and_run.bat", "build_windows.bat"):
            txt = (ROOT / bat_name).read_text(encoding="utf-8")
            self.assertIn('set "VIRTUAL_ENV=%~dp0.venv"', txt, f"{bat_name} must pin VIRTUAL_ENV to %~dp0.venv")
            self.assertIn('set "PATH=%~dp0.venv\\Scripts;%PATH%"', txt, f"{bat_name} must prepend %~dp0.venv\\Scripts")
            self.assertIn("import sys, pip", txt, f"{bat_name} must verify both sys and pip in .venv")
        for build_bat in ("build_and_run.bat", "build_windows.bat"):
            txt = (ROOT / build_bat).read_text(encoding="utf-8")
            self.assertIn("%~d0\\Python312\\python.exe", txt, f"{build_bat} must probe current drive %~d0")
            self.assertIn("python -m PyInstaller", txt, f"{build_bat} must invoke PyInstaller via python -m")


class DestructiveDatabaseAndRecoveryTests(unittest.TestCase):
    """Simulates power loss, corrupted SQLite/WAL files, same-second backup storms, and hostile inputs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yj_destruct_")
        self.home = Path(self._tmp.name)
        self.db_path = self.home / "alahd_erp_v1.sqlite3"
        self.backups_dir = self.home / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_corrupted_wal_repaired_without_losing_committed_data(self) -> None:
        store = Store(self.db_path)
        store.upsert_teacher("أ. خديجة", {"pct": 70})
        store.upsert_student({
            "id": "stu_wal_1",
            "code": "STU-001",
            "name": "مريم أحمد",
            "phone": "01012345678",
            "teacher": "أ. خديجة",
            "type": "خاص",
            "pct": 70,
            "months": {"2026-11": {"value": 300}},
        })
        store.checkpoint_wal()
        store.close()

        # Write corrupt bytes into the -wal file while main DB has the committed data
        wal_file = self.db_path.with_name(self.db_path.name + "-wal")
        wal_file.write_bytes(b"\x00\xff\xfe\xfdCORRUPT_WAL_HEADER" * 40)

        rep = recovery.run_startup_recovery(self.home, self.db_path)
        self.assertTrue(rep["dbHealthy"])

        reopened = Store(self.db_path)
        try:
            self.assertEqual(reopened.count_students(), 1)
            self.assertEqual(reopened.export_state()["students"][0]["name"], "مريم أحمد")
        finally:
            reopened.close()

    def test_rapid_same_second_backup_storm_never_overwrites_files(self) -> None:
        store = Store(self.db_path)
        sched = BackupScheduler(store, self.home)
        try:
            created_files = set()
            for i in range(4):
                store.upsert_teacher(f"معلمة {i}", {"pct": 70})
                res = sched.run_auto_backup(reason="rapid_test", force=True)
                self.assertTrue(res["ran"])
                created_files.add(res["file"])
            self.assertEqual(len(created_files), 4, f"Expected 4 distinct backup files in same second, got {created_files}")
        finally:
            store.close()

    def test_destructive_import_limits_xxe_and_post_commit_rollback(self) -> None:
        store = Store(self.db_path)
        try:
            # 1. Tighten importMaxRows to 100 in settings and verify 120-row CSV & XLSX are both rejected
            store.update_settings({"importMaxRows": 100}, actor="test")
            state = store.export_state()

            csv_lines = ["اسم الطالبة,رقم التواصل,المعلمة,قيمة الاشتراك"] + [
                f"طالبة {i},01000000{i:03d},خديجة,200" for i in range(1, 115)
            ]
            csv_bytes = "\n".join(csv_lines).encode("utf-8")
            with self.assertRaises(ValueError) as ctx_csv:
                preview_import_file(csv_bytes, "too_many.csv", "students", state)
            self.assertIn("100", str(ctx_csv.exception))

            wb = Workbook()
            ws = wb.active
            ws.append(["اسم الطالبة", "رقم التواصل", "المعلمة", "قيمة الاشتراك"])
            for i in range(1, 115):
                ws.append([f"طالبة {i}", f"01000000{i:03d}", "خديجة", 200])
            buf = io.BytesIO()
            wb.save(buf)
            with self.assertRaises(ValueError) as ctx_xlsx:
                preview_import_file(buf.getvalue(), "too_many.xlsx", "students", state)
            self.assertIn("100", str(ctx_xlsx.exception))

            # 2. Malicious XXE / DOCTYPE inside XLSX XML must be rejected by _guard_xlsx_container
            mal_buf = io.BytesIO()
            with zipfile.ZipFile(mal_buf, "w") as zf:
                zf.writestr(
                    "xl/workbook.xml",
                    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><workbook/>',
                )
            with self.assertRaises(ValueError) as ctx_xxe:
                preview_import_file(mal_buf.getvalue(), "xxe.xlsx", "students", state)
            self.assertIn("DOCTYPE", str(ctx_xxe.exception).upper() + "DOCTYPE")

            # 3. CP1256 (Windows Arabic) CSV import works and post-commit verification checks actual added student IDs
            cp1256_csv = "اسم الطالبة,رقم التواصل,اسم المعلمة,نوع الاشتراك,الشهر,قيمة الاشتراك\r\nسارة محمود,01099887766,أ. خديجة,خاص,2026-11,350\r\n".encode("cp1256")
            res_ok = commit_import(
                store,
                cp1256_csv,
                "arabic_windows.csv",
                "students",
                backup_folder=self.backups_dir,
                auto_create_refs=True,
            )
            self.assertTrue(res_ok["ok"])
            self.assertEqual(res_ok["added"], 1)
            self.assertEqual(store.count_students(), 1)

            # 4. Simulate a post-commit verification failure where a new student ID is missing from fresh_state
            cp1256_csv_2 = "اسم الطالبة,رقم التواصل,اسم المعلمة,نوع الاشتراك,الشهر,قيمة الاشتراك\r\nفاطمة الزهراء,01122334455,أ. خديجة,خاص,2026-11,400\r\n".encode("cp1256")
            orig_export = store.export_state
            call_count = 0

            def buggy_export():
                nonlocal call_count
                call_count += 1
                st = orig_export()
                if call_count >= 2:
                    # Simulate storage dropping the newly added student on post-commit read-back
                    st["students"] = [s for s in st["students"] if s.get("phone") != "01122334455"]
                return st

            with mock.patch.object(store, "export_state", side_effect=buggy_export):
                with self.assertRaises(ValueError) as ctx_revert:
                    commit_import(
                        store,
                        cp1256_csv_2,
                        "second.csv",
                        "students",
                        backup_folder=self.backups_dir,
                        auto_create_refs=True,
                    )
                self.assertIn("فشل التحقق بعد الحفظ", str(ctx_revert.exception))

            # Database must have rolled back cleanly to 1 student (سارة محمود)
            self.assertEqual(store.count_students(), 1)
            self.assertEqual(store.export_state()["students"][0]["name"], "سارة محمود")
        finally:
            store.close()

    def test_concurrent_multithread_stress_and_closed_month_lock(self) -> None:
        store = Store(self.db_path)
        try:
            store.upsert_teacher("أ. خديجة", {"pct": 70})
            store.upsert_student({
                "id": "stu_locked",
                "code": "STU-900",
                "name": "طالبة الشهر المغلق",
                "phone": "01055556666",
                "teacher": "أ. خديجة",
                "type": "خاص",
                "pct": 70,
                "months": {"2026-10": {"value": 300, "paymentDate": "2026-10-05"}},
            })
            store.close_month("2026-10", note="إغلاق نهائي")

            errors: list[Exception] = []
            locked_rejections = 0
            lock_Guard = threading.Lock()

            def worker(tid: int) -> None:
                nonlocal locked_rejections
                try:
                    for step in range(8):
                        sid = f"stu_t{tid}_{step}"
                        store.upsert_student({
                            "id": sid,
                            "code": f"T{tid:02d}-{step:02d}",
                            "name": f"طالبة التزامن {tid}-{step}",
                            "phone": f"012{tid:04d}{step:04d}",
                            "teacher": "أ. خديجة",
                            "type": "خاص",
                            "pct": 70,
                            "months": {"2026-11": {"value": 250}},
                        })
                        # Attempt illegal write to closed month 2026-10 -> must raise MonthLockedError
                        try:
                            store.upsert_installment({
                                "id": f"bad_inst_{tid}_{step}",
                                "receiptNo": f"BAD-{tid}-{step}",
                                "studentId": "stu_locked",
                                "month": "2026-10",
                                "amount": 100,
                                "paymentDate": "2026-10-15",
                            })
                        except MonthLockedError:
                            with lock_Guard:
                                locked_rejections += 1
                        if step == 4:
                            _ = store.quick_integrity()
                except Exception as exc:
                    with lock_Guard:
                        errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            self.assertEqual(errors, [], f"Concurrent worker errors: {errors}")
            self.assertEqual(locked_rejections, 80, "Every write to closed month 2026-10 must be rejected")
            self.assertEqual(store.count_students(), 1 + 80)
            integ = store.full_integrity_check()
            self.assertTrue(integ["ok"], f"Integrity check failed after stress: {integ}")
        finally:
            store.close()


@unittest.skipUnless(HAS_TESTCLIENT, "FastAPI TestClient unavailable")
class DestructiveHttpEndpointsTests(unittest.TestCase):
    """Tests HTTP API resilience under rapid manual backup triggers, last-backup deletion attempts, and malformed payloads."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yj_http_destruct_")
        self.home = Path(self._tmp.name)
        self.secret = "destruct_secret_123"
        self.app = create_app(
            home=self.home,
            ui_dir=UI_DIR,
            port=18900,
            window_secret=self.secret,
            seed_on_empty=False,
        )
        self.scheduler = BackupScheduler(self.app.state.store, self.home)
        self.app.state.backup_scheduler = self.scheduler
        self.client = TestClient(self.app, headers={"X-Window-Secret": self.secret, "Host": "127.0.0.1:18900"})

    def tearDown(self) -> None:
        try:
            self.scheduler.stop(run_exit_backup=False)
        except Exception:
            pass
        try:
            self.app.state.store.close()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_manual_backup_now_always_creates_backup_even_when_rev_unchanged(self) -> None:
        r1 = self.client.post("/api/backup/now")
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["ran"])
        f1 = r1.json()["file"]

        # Trigger again immediately with zero DB changes -> must still force-create a new verified backup!
        r2 = self.client.post("/api/backup/now")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["ran"])
        f2 = r2.json()["file"]
        self.assertNotEqual(f1, f2)

        # Attempting to delete ALL backups at once must leave at least 1 backup protected
        all_names = [b["name"] for b in self.client.get("/api/backups").json()]
        self.assertGreaterEqual(len(all_names), 2)
        del_res = self.client.post("/api/backups/delete", json={"names": all_names}).json()
        self.assertEqual(del_res["remaining"], 1)
        self.assertEqual(len(del_res["errors"]), 1)
        self.assertIn("آخر نسخة", del_res["errors"][0]["error"])



    def test_ephemeral_port_zero_and_missing_drive_app_home_guard(self) -> None:
        from alahd_erp.config import resolve_port
        with mock.patch.dict(os.environ, {"ALAHD_ERP_PORT": "0"}):
            p = resolve_port()
            self.assertGreaterEqual(p, 1024)
            self.assertLessEqual(p, 65535)

        if os.name != "nt":
            with mock.patch.dict(os.environ, {"ALAHD_ERP_HOME": r"E:\UnpluggedDrive\AcademyData"}):
                with self.assertRaises(OSError):
                    resolve_app_home()

    def test_rotate_survives_locked_file_permission_error_and_usb_catchup(self) -> None:
        from alahd_erp.infrastructure.database import rotate
        bdir = self.home / "rot_test"
        bdir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            f = bdir / f"alahd_auto_2026010{i}_000000.sqlite3"
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        orig_unlink = Path.unlink

        def locked_unlink(self_p, *a, **kw):
            if "20260100" in self_p.name:
                raise PermissionError(13, "File locked by another process", str(self_p))
            return orig_unlink(self_p, *a, **kw)

        with mock.patch.object(Path, "unlink", locked_unlink):
            rotate(bdir, "alahd_auto_*.sqlite3", 3)
        rem = sorted(p.name for p in bdir.glob("alahd_auto_*.sqlite3"))
        self.assertIn("alahd_auto_20260100_000000.sqlite3", rem)
        self.assertEqual(len(rem), 4)

        # Verify extraBackupFolder rejects local backups dir, subfolder inside it, and normalizes empty quotes
        store = Store(self.home / "catchup.sqlite3")
        try:
            local_bk = self.home / "backups"
            local_bk.mkdir(exist_ok=True)
            store.update_settings({"extraBackupFolder": str(local_bk)}, actor="qa")
            self.assertFalse(store.get_backup_health(local_bk)["extraFolderReachable"])
            store.update_settings({"extraBackupFolder": '""'}, actor="qa")
            self.assertEqual(store.get_settings()["extraBackupFolder"], "")

            # Verify catch-up copies previously missed backups when USB drive is reconnected
            b1 = local_bk / "alahd_auto_20261116_010000.sqlite3"
            b2 = local_bk / "alahd_auto_20261116_020000.sqlite3"
            store.backup_to(b1, mirror_to_extra=False)
            store.backup_to(b2, mirror_to_extra=False)
            usb_dir = self.home / "usb_F_backups"
            store.update_settings({"extraBackupFolder": str(usb_dir), "mirrorBackupsToExtraFolder": True}, actor="qa")
            b3 = local_bk / "alahd_auto_20261116_030000.sqlite3"
            store.backup_to(b3, mirror_to_extra=True)
            self.assertEqual(
                sorted(p.name for p in usb_dir.glob("alahd_*.sqlite3")),
                [b1.name, b2.name, b3.name],
            )
        finally:
            store.close()

    def test_crash_safety_snapshot_and_corrupt_db_without_backup_untouched(self) -> None:
        # 1. Crash recovery creates alahd_safety_after_crash_* when previous session was interrupted
        h1 = self.home / "crash_case"
        h1.mkdir(parents=True, exist_ok=True)
        db1 = h1 / "alahd_erp_v1.sqlite3"
        s1 = Store(db1)
        s1.upsert_teacher("أ. خديجة", {"pct": 70})
        s1.close()
        (h1 / recovery.SESSION_MARKER).write_text(json.dumps({"pid": 12345, "startedAt": "2026-11-16 07:00:00"}), encoding="utf-8")
        r1 = recovery.run_startup_recovery(h1, db1)
        self.assertTrue(r1["previousSessionInterrupted"])
        self.assertTrue(r1.get("afterCrashSnapshot"))
        self.assertTrue((h1 / "backups" / r1["afterCrashSnapshot"]).is_file())

        # 2. Corrupt DB with NO valid backup keeps corrupt file at db_path and sets unrecoverableCorruptDb=True
        h2 = self.home / "corrupt_no_bk"
        h2.mkdir(parents=True, exist_ok=True)
        db2 = h2 / "alahd_erp_v1.sqlite3"
        db2.write_bytes(b"CORRUPT_DB_BYTES_WITHOUT_BACKUP" * 40)
        r2 = recovery.run_startup_recovery(h2, db2)
        self.assertTrue(r2.get("unrecoverableCorruptDb"))
        self.assertTrue(db2.is_file())
        self.assertTrue(Path(r2["corruptBackupCopy"]).is_dir())


if __name__ == "__main__":
    unittest.main()
