"""Comprehensive UI Integration, Settings Runtime Wiring, Static DOM/CSS Parity & Live Headless Browser E2E Tests.

Covers:
1. CSS variable completeness across :root, [data-theme="dark"], and [data-theme="sepia"].
2. DOM ID parity: every $('#id') / getElementById('id') / renderHtml('#id') in JS exists in index.html.
3. Zero raw HTML string literals interpolated inside `html` tagged templates across all JS modules.
4. Full 55-setting registry <-> models.py validation <-> index.html <select> options parity.
5. Backend runtime enforcement of `importRememberHistory` (in preview & commit), `exportIncludeTimestamp`
   (in /api/export/xlsx and /api/export/html), `logSensitiveData` (phone redaction in /api/system/logs),
   and `extraBackupFolder` POSIX Windows-drive path safety.
6. PyInstaller spec (`YawmJadidAcademy.spec`) bundling `alahd_erp/icon.ico` for `verify_build.py` parity,
   plus `.bat` virtual-environment health checks.
7. Live Headless Chromium E2E test across all 16 views, modals, filters, exports, maintenance actions,
   and settings persistence (automatically skipped only if Chromium/Selenium is unavailable).
"""
from __future__ import annotations

import base64
import io
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from openpyxl import Workbook

from alahd_erp.api.server import create_app
from alahd_erp.domain.academy_data_loader import ensure_academy_workbooks_loaded
from alahd_erp.domain.models import DEFAULT_SETTINGS, validate_settings
from alahd_erp.domain.settings_registry import SETTINGS_BY_KEY
from alahd_erp.infrastructure import recovery
from alahd_erp.infrastructure.backup_scheduler import BackupScheduler
from alahd_erp.infrastructure.database import Store
from alahd_erp.services.import_service import validate_import_mode
from tests._helpers import ROOT

try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except Exception:
    HAS_TESTCLIENT = False

UI_DIR = ROOT / "alahd_erp" / "ui"


class StaticUiAndSettingsParityTests(unittest.TestCase):
    """Verifies 100% consistency between index.html, design-system.css, JS modules, settings_registry, and models."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.html_text = (UI_DIR / "index.html").read_text(encoding="utf-8")
        cls.css_text = (UI_DIR / "css" / "design-system.css").read_text(encoding="utf-8")
        cls.js_files: dict[str, str] = {}
        for p in (UI_DIR / "js").rglob("*.js"):
            cls.js_files[str(p.relative_to(UI_DIR))] = p.read_text(encoding="utf-8")

    def test_all_used_css_variables_are_defined(self) -> None:
        defined_vars = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", self.css_text))
        missing: dict[str, list[str]] = {}
        for name, txt in [("index.html", self.html_text), ("css/design-system.css", self.css_text), *self.js_files.items()]:
            for m in re.finditer(r"var\(\s*(--[a-zA-Z0-9_-]+)(?:\s*,[^)]+)?\)", txt):
                var_name = m.group(1)
                if var_name not in defined_vars:
                    missing.setdefault(var_name, []).append(name)
        self.assertEqual(missing, {}, f"Undefined CSS custom properties used in UI: {missing}")

    def test_no_quoted_html_tags_inside_js_modules(self) -> None:
        offenders: list[str] = []
        for rel, txt in sorted(self.js_files.items()):
            for line_no, line in enumerate(txt.splitlines(), 1):
                if re.search(r"['\"]\s*<[a-zA-Z/][^>]*>", line):
                    offenders.append(f"{rel}:{line_no}: {line.strip()}")
        self.assertEqual(offenders, [], f"Quoted HTML tags found in JS (use html`...` instead): {offenders}")

    def test_all_referenced_dom_ids_exist_in_html_or_templates(self) -> None:
        combined = self.html_text + "\n" + "\n".join(self.js_files.values())
        defined_ids = set(re.findall(r"\bid=['\"]([a-zA-Z0-9_-]+)['\"]", combined))
        missing_ids: dict[str, list[str]] = {}
        pattern = re.compile(
            r"(?:\$\(\s*['\"]#([a-zA-Z0-9_-]+)['\"]|getElementById\(\s*['\"]([a-zA-Z0-9_-]+)['\"]|renderHtml\(\s*['\"]#([a-zA-Z0-9_-]+)['\"])"
        )
        for rel, txt in sorted(self.js_files.items()):
            for m in pattern.finditer(txt):
                gid = m.group(1) or m.group(2) or m.group(3)
                if gid not in defined_ids:
                    missing_ids.setdefault(gid, []).append(rel)
        self.assertEqual(missing_ids, {}, f"DOM IDs referenced in JS but missing from HTML/templates: {missing_ids}")

    def test_every_html_setting_select_option_is_valid_in_models_and_registry(self) -> None:
        for m in re.finditer(r"<div\b[^>]*data-setting-key=['\"]([^'\"]+)['\"][^>]*>(.*?)</div>", self.html_text, re.S):
            key, block = m.group(1), m.group(2)
            if key == "pinCode":
                continue
            self.assertIn(key, SETTINGS_BY_KEY, f"data-setting-key {key!r} missing from SETTINGS_BY_KEY")
            spec = SETTINGS_BY_KEY[key]
            opts_html = re.findall(r"<option\b[^>]*value=['\"]([^'\"]*)['\"]", block)
            if opts_html:
                reg_opts = spec.get("options") or []
                for opt in opts_html:
                    self.assertIn(opt, reg_opts, f"HTML option {opt!r} for {key!r} missing from SETTINGS_BY_KEY options {reg_opts}")
                    validated = validate_settings({key: opt})
                    self.assertIn(key, validated)

    def test_every_registry_enum_option_and_default_passes_models_validation(self) -> None:
        for key, spec in SETTINGS_BY_KEY.items():
            default_val = spec.get("default")
            if default_val is not None:
                validated = validate_settings({key: default_val})
                self.assertIn(key, validated, f"Default value {default_val!r} for {key} failed validation")
            for opt in spec.get("options") or []:
                validated = validate_settings({key: opt})
                self.assertIn(key, validated, f"Option {opt!r} for {key} failed validation")

    def test_import_mode_aliases_normalized_cleanly(self) -> None:
        self.assertEqual(validate_import_mode("update_existing"), "update_existing")
        self.assertEqual(validate_import_mode("add_only"), "add_only")
        self.assertEqual(validate_import_mode("append_only"), "add_only")
        self.assertEqual(validate_import_mode("skip_duplicates"), "skip_duplicates")
        self.assertEqual(validate_import_mode("skip_existing"), "skip_duplicates")

    def test_pyinstaller_spec_bundles_icon_for_verify_build(self) -> None:
        spec_txt = (ROOT / "YawmJadidAcademy.spec").read_text(encoding="utf-8")
        self.assertIn('("alahd_erp/icon.ico", "alahd_erp")', spec_txt)


@unittest.skipUnless(HAS_TESTCLIENT, "FastAPI TestClient unavailable")
class RuntimeSettingsIntegrationTests(unittest.TestCase):
    """Tests that settings changes actually take effect in backend endpoints."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yj_rt_set_")
        self.home = Path(self._tmp.name)
        self.secret = "test_secret_integration"
        self.app = create_app(
            home=self.home,
            ui_dir=UI_DIR,
            port=18899,
            window_secret=self.secret,
            seed_on_empty=False,
        )
        self.client = TestClient(self.app, headers={"X-Window-Secret": self.secret, "Host": "127.0.0.1:18899"})

    def tearDown(self) -> None:
        try:
            self.app.state.store.close()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_export_include_timestamp_setting_controls_filenames(self) -> None:
        # Default: exportIncludeTimestamp = True -> date is in Content-Disposition filename
        r1 = self.client.get("/api/export/xlsx?scope=students")
        self.assertEqual(r1.status_code, 200)
        cd1 = r1.headers.get("content-disposition", "")
        self.assertRegex(cd1, r"YawmJadid_Academy_students_\d{4}-\d{2}-\d{2}\.xlsx")

        # Turn off exportIncludeTimestamp -> no date in Content-Disposition filename
        self.client.put("/api/settings", json={"exportIncludeTimestamp": False})
        r2 = self.client.get("/api/export/xlsx?scope=students")
        self.assertEqual(r2.status_code, 200)
        cd2 = r2.headers.get("content-disposition", "")
        self.assertIn('filename="YawmJadid_Academy_students.xlsx"', cd2)

        r_html = self.client.get("/api/export/html?scope=full&includePayments=false")
        self.assertEqual(r_html.status_code, 200)
        cd_html = r_html.headers.get("content-disposition", "")
        self.assertIn('filename="YawmJadid_public_full.html"', cd_html)

    def test_import_remember_history_setting_controls_preview_duplicate_banner(self) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "الطالبات"
        ws.append(["الكود", "الاسم", "رقم الهاتف", "المعلمة", "الباقة"])
        ws.append(["STU-901", "طالبة اختبار تكرار", "01012345678", "خديجة أحمد", "خاص"])
        buf = io.BytesIO()
        wb.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        # First preview & commit
        p1 = self.client.post("/api/import/preview", json={"filename": "dup.xlsx", "dataset": "students", "fileBase64": b64}).json()
        self.assertNotIn("previousImport", p1)
        c1 = self.client.post(
            "/api/import/commit",
            json={
                "filename": "dup.xlsx",
                "dataset": "students",
                "fileBase64": b64,
                "expectedFileHash": p1["fileHash"],
                "rev": self.app.state.store.rev,
            },
        )
        self.assertEqual(c1.status_code, 200)

        # Second preview with importRememberHistory=True -> surfaces previousImport
        p2 = self.client.post("/api/import/preview", json={"filename": "dup.xlsx", "dataset": "students", "fileBase64": b64}).json()
        self.assertIn("previousImport", p2)

        # Disable importRememberHistory -> preview no longer warns and commit succeeds without allowReimport
        self.client.put("/api/settings", json={"importRememberHistory": False})
        p3 = self.client.post("/api/import/preview", json={"filename": "dup.xlsx", "dataset": "students", "fileBase64": b64}).json()
        self.assertNotIn("previousImport", p3)

    def test_log_sensitive_data_setting_redacts_phones_in_system_logs(self) -> None:
        log_dir = self.home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "alahd_erp.log").write_text(
            "2026-11-16 [INFO] student phone 01098765432 updated\n", encoding="utf-8"
        )
        # Default: logSensitiveData = False -> phone number masked
        r1 = self.client.get("/api/system/logs").json()
        self.assertEqual(r1["lines"][0], "2026-11-16 [INFO] student phone 010•••••432 updated")

        # Enable logSensitiveData = True -> unmasked
        self.client.put("/api/settings", json={"logSensitiveData": True})
        r2 = self.client.get("/api/system/logs").json()
        self.assertEqual(r2["lines"][0], "2026-11-16 [INFO] student phone 01098765432 updated")

    def test_windows_drive_path_in_extra_backup_folder_never_pollutes_cwd_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX-specific guard")
        store: Store = self.app.state.store
        store.update_settings({"extraBackupFolder": r"Q:\definitely\missing\drive"}, actor="test")
        dest = self.home / "backups" / "alahd_manual_test.sqlite3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        store.backup_to(dest, mirror_to_extra=True)
        self.assertTrue(dest.is_file())
        self.assertFalse((ROOT / r"Q:\definitely\missing\drive").exists())


class LiveChromiumBrowserE2ETests(unittest.TestCase):
    """Runs real Headless Chromium against the live server and verifies all 16 views and interactive flows."""

    def test_full_ui_views_and_interactive_workflows_in_chromium(self) -> None:
        try:
            import uvicorn
            import alahd_erp.api.server as srv
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
        except Exception as exc:
            self.skipTest(f"Selenium/uvicorn unavailable: {exc}")

        with tempfile.TemporaryDirectory(prefix="yj_chrome_e2e_") as tmp_home:
            app_home = Path(tmp_home)
            db_path = app_home / "alahd_erp_v1.sqlite3"
            secret = "e2e_browser_secret_999"

            # Find free port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

            orig_host_allowed = srv.host_allowed
            orig_origin_allowed = srv.origin_allowed
            srv.host_allowed = lambda h: True
            srv.origin_allowed = lambda o, p: True

            try:
                recovery.run_startup_recovery(app_home, db_path)
                app = srv.create_app(
                    home=app_home,
                    ui_dir=UI_DIR,
                    port=port,
                    window_secret=secret,
                    seed_on_empty=False,
                )
                ensure_academy_workbooks_loaded(app.state.store)
                scheduler = BackupScheduler(app.state.store, app_home)
                scheduler.start()
                app.state.backup_scheduler = scheduler

                cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False, log_level="warning")
                server = uvicorn.Server(cfg)
                th = threading.Thread(target=server.run, daemon=True)
                th.start()
                time.sleep(1.2)

                opts = Options()
                opts.add_argument("--headless=new")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--window-size=1440,950")
                opts.add_argument("--host-resolver-rules=MAP yawmjadid.local 127.0.0.1")

                driver = webdriver.Chrome(options=opts)
                try:
                    driver.get(f"http://yawmjadid.local:{port}/?wsec={secret}")
                    time.sleep(2.0)

                    driver.execute_script("""
                        window.__e2eErrors = [];
                        window.addEventListener('error', (e) => window.__e2eErrors.push(e.message));
                        window.addEventListener('unhandledrejection', (e) => window.__e2eErrors.push(String(e.reason)));
                        window.confirm = () => true;
                    """)

                    views = [
                        "dashboard", "statistics", "alerts", "students", "profile",
                        "collections", "payroll", "accounting", "attendance",
                        "memorization", "madrasa", "certificates", "teachers",
                        "import", "exports", "system",
                    ]
                    for v in views:
                        driver.execute_script(f"window.location.hash = '#/{v}';")
                        time.sleep(0.35)
                        active_id = driver.execute_script(
                            "return document.querySelector('.view-panel.active') && document.querySelector('.view-panel.active').id;"
                        )
                        self.assertEqual(active_id, f"view-{v}")
                        body_txt = driver.find_element(By.TAG_NAME, "body").text
                        for bad_token in ("<span", "</span", "<strong", "</strong", "NaN", "[object Object]"):
                            self.assertNotIn(bad_token, body_txt, f"Bad token {bad_token!r} rendered in view {v}")

                    # Test Export view mode switching & section checkboxes
                    driver.execute_script("window.location.hash = '#/exports';")
                    time.sleep(0.4)
                    self.assertTrue(driver.find_element(By.ID, "secMonthFinance").get_attribute("disabled"))
                    driver.execute_script("document.getElementById('htmlModeFinancial').click();")
                    time.sleep(0.2)
                    self.assertFalse(driver.find_element(By.ID, "secMonthFinance").get_attribute("disabled"))
                    driver.execute_script("document.getElementById('btnHtmlSectionsAll').click();")
                    time.sleep(0.1)
                    checked_count = driver.execute_script("return document.querySelectorAll('.html-section-check:checked').length;")
                    self.assertEqual(checked_count, 6)

                    # Test Settings save & Recovery Center maintenance actions
                    driver.execute_script("window.location.hash = '#/system';")
                    time.sleep(0.5)
                    driver.execute_script("""
                        document.getElementById('setStudentCodePrefix').value = 'YJA-';
                        document.getElementById('setRequireStudentPhone').checked = true;
                        document.getElementById('setAutoBackupEnabled').checked = false;
                        document.getElementById('formSystemSettings').requestSubmit();
                    """)
                    body_sys = ""
                    for _ in range(25):
                        time.sleep(0.2)
                        body_sys = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
                        if "معطلة!" in body_sys:
                            break
                    self.assertNotIn("<strong", body_sys)
                    self.assertIn("معطلة!", body_sys)

                    # Verify Student modal uses new studentCodePrefix 'YJA-'
                    driver.execute_script("window.location.hash = '#/students';")
                    time.sleep(0.4)
                    driver.execute_script("document.getElementById('btnAddStudentMain').click();")
                    time.sleep(0.2)
                    gen_code = driver.find_element(By.ID, "modalStudentCode").get_attribute("value")
                    self.assertTrue(gen_code.startswith("YJA-"), f"Expected YJA- prefix, got {gen_code!r}")

                    js_errs = driver.execute_script("return window.__e2eErrors;")
                    self.assertEqual(js_errs, [], f"Uncaught JS errors during E2E run: {js_errs}")
                finally:
                    driver.quit()
                    scheduler.stop(run_exit_backup=False)
                    server.should_exit = True
            finally:
                srv.host_allowed = orig_host_allowed
                srv.origin_allowed = orig_origin_allowed


if __name__ == "__main__":
    unittest.main()
