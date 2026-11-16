# Test Report — Yawm Jadid Academy v1.3.0

**Environment:** Linux x86_64 (5.10 kernel), Python 3.12.16, pytest 9 + stdlib unittest suite
**Date:** 2026-11-16 · **Runner:** `python -m pytest tests/ -q`

---

## 1. Headline numbers

| Suite | Result |
|---|---|
| Full suite | **438 passed, 7 xfailed, 1 skipped (446 total)** (expected failures, intentional), 33 subtests — 36.5 s |
| Collected | 446 tests |
| Baseline (v1.2.1 source) | 387 passed, 7 xfailed → **all pre-existing tests still green** |
| New acceptance suite `tests/test_resilience.py` | **40/40 passed** |

The 7 xfailed are documented, intentional expected-failures carried from v1.2.x (see their docstrings; unchanged behaviour).

## 2. What the acceptance suite proves (per release requirement)

### §3 Import (class `ImportResilience`, 6 tests)
- **Duplicate-file guard**: committing the identical file+dataset twice raises an explicit Arabic error; `allow_reimport=True` succeeds and stays idempotent (no duplicate student row); the same file for a *different* dataset is not blocked.
- **Preview binding**: a hash mismatch after preview is rejected («تغير محتوى الملف بعد المعاينة»).
- **Crash marker**: `import_in_progress.json` exists only during the commit window and is removed after success; startup recovery detects a leftover marker (see §3 below).
- **Post-commit verification + auto-revert**: a simulated post-commit read failure (monkeypatched `export_state`) triggers the safety net — ValueError with Arabic message AND the database reverts to the pre-import snapshot (student NOT present afterwards).
- **Settings-driven limits**: `importMaxFileMB`/`importMaxRows` honoured; hard clamps (200 MB / 200k rows) always win over nonsense; oversized file rejected before any parsing.

### §4 Export (class `ExportVerification`, 4 tests)
- `verify_workbook_bytes` rejects garbage and empty bytes; a real export re-opens cleanly.
- Exported workbooks never contain a live formula, even when a student name is `=HYPERLINK(...)` (injection neutralised).
- Atomic mirror copy: byte-identical verified copy, `.part` cleaned; a simulated write failure never corrupts the previously good mirror copy.

### §5 Public HTML privacy (class `HtmlPrivacySentinels`, 5 tests)
- Distinctive financial sentinels (amounts `123456/654321/111222`, receipt `RCP-SENTINEL-99`, payment date `2026-11-05`) are searched across the **complete generated file** — markup, comments, attributes, scripts, hidden elements: **zero occurrences in Mode A**; all present in Mode B; legacy scope-only calls keep the old behaviour (financial content + masked phones).
- Mode A drops financial sections entirely (`sec-month-finance`, `sec-collections`, `sec-payroll` absent), keeps non-financial identity data (name, masked phone `010•••••077`, code), carries `privacy-mode: public` + the share-safe banner; Mode B carries `privacy-mode: financial` + the sensitive-data warning.
- Section selection: requested-but-disabled sections reported in the head comment (`disabled-by-privacy: collections`); student 360° card in Mode A has no receipts table and no amounts.

### §6 Backup / recovery (classes `BackupSchedulerResilience`, `CorruptBackupHandling`, `StartupRecoveryScenarios`, 17 tests)
- Scheduler: live settings read every cycle (interval 5 min honoured), garbled settings fall back to safe defaults (30 min / keep 48), retention floor 3, unchanged-DB skip, exit backup honours `backupOnExit`.
- **Failure isolation**: an unwritable backups folder fails the backup, increments the consecutive-failure counter, and leaves the live database healthy; recovery of the folder restores success and resets the counter. Mirror failure (file-as-parent-directory) logs a warning and the primary backup still verifies.
- Corrupt/zero-byte backups fail verification; restore refuses them and the live data is untouched.
- Startup recovery scenarios: clean DB OK; unclean session marker detected; **corrupt DB → quarantined (preserved) + newest verified backup restored + Store opens with the restored data**; missing DB restored from backup; missing DB without backups = fresh first run (not an error); stale orphan `.part` swept (fresh one kept); interrupted-import marker surfaced + archived + cleared; report persisted to `logs/last_recovery.json` and re-readable.

### §7 Settings-driven operation (classes `SettingsSafety`, `StoreMaintenancePrimitives`, 7 tests)
- Invalid backup settings (`interval=0`, `keep=1`, `maxMB=5000`) rejected with validation errors; valid settings persist across reopen; missing keys fall back to **safe** defaults (auto-backup ON, exit backup ON, HTML payments OFF).
- Maintenance primitives verified against a real store: full integrity check, WAL checkpoint, VACUUM, deep backup verification (kind/counts/schema version), import-history dedup keyed by (hash, dataset).

## 3. End-to-end smoke (live server, Linux)

Started the real entry point (`python -m alahd_erp.main`, no-UI mode) and exercised the HTTP API:

| Check | Result |
|---|---|
| `/api/health` | `ok:true`, version `1.3.0` |
| Startup log | recovery ran (first launch path), scheduler started, exit backup created on shutdown |
| `/api/system/health-report` | db quickCheck ok, 2 backups present (startup auto + daily), scheduler running |
| `/api/backup/now` | `ran:false, reason:"unchanged"` (correct dedup) |
| `/api/maintenance/integrity` | `ok:true`, 0 FK violations, schema v3 |
| `/api/recovery/status` | report readable, session marker present while running |
| `/api/export/html?includePayments=false` | HTTP 200, `privacy-mode: public` present |
| `/api/export/html?includePayments=true` | HTTP 200, `privacy-mode: financial` present |
| `/api/import/history` | ok, empty on fresh DB |
| `/api/backups/verify` (deep) | `ok:true`, integrity ok, required tables, schema v3 |
| `/api/settings/catalog` | 13 categories, 55 catalog entries |

## 4. Static / packaging checks (in-suite)

- `test_branding.py`: version `1.3.0` consistent across `__init__.py`, `pyproject.toml`, `version_info.txt`, `.iss`, `index.html`, `CHANGELOG`.
- `test_build_scripts.py`: `.bat` files ASCII + CRLF; pywebview optional; **new**: both build scripts wired to `scripts/verify_build.py`.
- `test_build_artifact_verifier.py`: the PyInstaller-archive inspector (forbidden payloads, module parity) still green after spec changes.
- `test_docs_in_sync.py`: route inventory (112 registrations) matches `docs/ARCHITECTURE.md`.
- `verify_build.py` additionally exercised against synthetic dist trees: missing files → exit 1; complete tree → 33/33 checks pass.

## 5. Not covered here (explicit)

- **Windows EXE build & runtime** — requires native Windows (`docs/BUILD_WINDOWS.md` §2–§3).
- Real hardware power-loss / OS-kill timing tests — see `KNOWN_LIMITATIONS.md` §2.
- Browser rendering of the new UI (no GUI in this environment); UI logic is exercised through the API-contract tests and JS syntax checks (`node --check` on all modules).

## 6. Deep UI Integration & Live Headless Chromium E2E (`tests/test_ui_integration_and_e2e.py`, 12 tests)

- **Static DOM / CSS / Settings Parity**:
  - Every CSS custom property (`var(--...)`) used across `index.html`, `design-system.css`, and all 11 JS modules is defined across `:root`, `[data-theme="dark"]`, and `[data-theme="sepia"]`.
  - Zero raw quoted HTML strings inside JS `html` tagged templates.
  - Every DOM ID referenced via `$(#id)`, `getElementById(id)`, or `renderHtml(#id)` exists in `index.html`.
  - 100% option parity across `SETTINGS_BY_KEY`, `validate_settings`, `validate_import_mode`, and `index.html` `<select>` controls.
  - `YawmJadidAcademy.spec` bundles `("alahd_erp/icon.ico", "alahd_erp")` so `scripts/verify_build.py` passes all 33 dist checks.
- **Runtime Settings Enforcement**:
  - `exportIncludeTimestamp` controls date suffixes in `/api/export/xlsx`, `/api/export/html`, CSV, and JSON downloads.
  - `importRememberHistory` controls duplicate-file warnings in both `/api/import/preview` and `/api/import/commit`.
  - `logSensitiveData` redacts phone numbers in `/api/system/logs` when disabled (default).
  - `extraBackupFolder` safely rejects unmounted Windows drive paths on POSIX without polluting the working tree.
- **Live Headless Chromium E2E**:
  - Exercises all 16 UI views (`dashboard`, `statistics`, `alerts`, `students`, `profile`, `collections`, `payroll`, `accounting`, `attendance`, `memorization`, `madrasa`, `certificates`, `teachers`, `import`, `exports`, `system`) against the live FastAPI + SQLite WAL + `BackupScheduler` backend, verifying zero JS console/runtime errors, zero raw HTML tag leaks, interactive HTML export privacy mode switching, Settings persistence, and custom `studentCodePrefix` in the Student modal.
