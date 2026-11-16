# Changelog

## 1.3.0 — 2026-11-16 — Data-safety, Settings Control Center & privacy export release
**Backup & crash recovery (تستحق الأولوية القصوى لحماية البيانات)**
- Startup crash recovery (`infrastructure/recovery.py`): detects an unclean previous session, verifies database
  integrity, repairs WAL state automatically, quarantines an unusable database (never deleted) and restores the
  newest verified backup only when truly necessary. Every action is reported in Settings → مركز الاستعادة.
- Settings-driven backup scheduler (`infrastructure/backup_scheduler.py`): interval, retention, exit backup,
  verification and mirroring are live-configurable (no restart). Retention floor of 3 means cleanup can never
  delete every recovery point; failures are isolated and surfaced in the health report.
- Verified atomic mirror to the secondary folder (`.part` + hash verify + atomic replace) — a crash mid-copy can
  no longer truncate a previously-good offsite copy; fsync on finalized backup files.
- New APIs: `/api/backups/verify`, `/api/backups/delete`, `/api/backup/now`, `/api/recovery/status`,
  `/api/maintenance/{integrity,checkpoint,vacuum}`, `/api/system/health-report`, `/api/import/history`.

**Settings Control Center (مركز الإعدادات المتقدم)**
- 15 new live settings with Arabic labels/descriptions: backup (7), import limits (3), export defaults (2),
  public-HTML privacy (2), sensitive logging (1) — all validated, persisted and audited.
- New Settings UI: مركز الاستعادة والصيانة (recovery status, health report, integrity/WAL/VACUUM actions,
  backup-now), backup history with per-file verify/delete/restore, import/export limit controls.

**Import hardening**
- Duplicate-file guard: a previously imported file (same bytes, same dataset) now warns on preview and requires
  explicit confirmation on commit (`allowReimport`).
- Crash marker (`import_in_progress.json`) + startup detection of interrupted imports.
- Post-commit verification: inserted rows re-read from the database; on mismatch the pre-import snapshot is
  restored automatically. Import size/row limits now configurable (hard safety clamps always apply).

**Export hardening**
- Every generated workbook is re-opened and verified before download; failure never produces a partial file.
- Public HTML export: two financial privacy modes — Mode A (public: every payment value removed from the markup,
  financial sections excluded, financial alerts filtered) and Mode B (explicit opt-in with an Arabic warning).
  Section-level selection (statistics / month_finance / students / collections / payroll / alerts) with
  auto-disabled financial sections in public mode; privacy mode recorded in the document metadata.

**Packaging**
- Version 1.3.0; build verification script (`scripts/verify_build.py`); installer unchanged GUID (upgrades in place).

## 1.2.1 - Windows launcher fixes
- Rewrote START_ACADEMY.bat / build scripts: ASCII + CRLF, optional pywebview, pre-flight check, /strict flag.
- SQLite read-only URIs built with Path.as_uri(); loopback probe bypasses system proxy.
- Removed heavy handoff/report docs. See CLAUDE_WORK_REPORT.md.

## 1.2.0 — 2026-11-14 — "Yawm Jadid" identity + production hardening
**Identity (أكاديمية يوم جديد — «يوم جديد هو عهد جديد»)**
- Name, tagline, logo (circular, transparent), banner, multi-size `icon.ico` (16–256), favicon, sage-olive/cream palette (light + dark),
  PIN-lock logo, welcome banner, Excel/HTML/certificate colours and default file-name prefixes (`YawmJadid_*`).
- Executable is now `YawmJadidAcademy.exe`. **The data folder stays `%LOCALAPPDATA%\AlAhdAcademySuite`** (`DATA_DIR_NAME`) so
  existing installs keep every record. Existing databases keep the academy name already saved in Settings.
**Security**
- F-03: the per-launch window secret is no longer accepted from the query string on API/static routes (header or httpOnly cookie
  only); `?wsec=` works on bootstrap paths only, and the UI removes it from the address bar right after load.
**Backups**
- DEFECT-B1: backups/restores no longer leave `*.part-wal`/`*.part-shm`/`-wal`/`-shm` files next to backup files; leftovers from
  <= 1.1.0 are swept on the next backup; `rotate()` removes companions of deleted backups.
**Export / Import fixes**
- Excel export (`full` and `teacher` scopes) no longer aborts when a teacher's name contains a character Excel forbids in sheet
  titles (`\ / ? * [ ] :`, e.g. «أ/ فاطمة»); such characters become `-`, edge apostrophes are dropped, valid names are unchanged.
- The import error-report workbook now writes text beginning with `=` (reasons, uploaded file name) as plain text, never a live formula.
**Build kit**: `YawmJadidAcademy.spec`, `installer/version_info.txt`, `installer/YawmJadidAcademy.iss`, `build_windows.bat`,
`requirements*.txt`, `docs/RELEASE_CHECKLIST.md`.
**Tests**: Python↔JS finance parity, WAL/backup/reset, F-03 frontend + HTTP, branding/packaging consistency, import/export (37).
**Build & Run**: `build_and_run.bat` (one click: venv → tests → PyInstaller → verify → smoke → portable zip → launch; flags `/fast` `/run` `/dev` `/nopause`).
**Session 13 (QA run: 145 engine scenarios + 67 real-browser UI checks)**
- FIX: the PIN lock screen never appeared after restarting the app (UI read `pinEnabled`, server sends `pinRequired`) — `ui/js/modules/store.js`; regression test `tests/test_pin_state_contract.py`.
- FIX: a student name > 120 chars or phone > 40 chars produced a generic "internal error"; now a clear Arabic validation message (`Store._put_student_in_tx`), and any other DB CHECK/UNIQUE violation is returned as HTTP 400 instead of 500 (`api/server.py`; needs the HTTP suite on Windows to re-confirm).
- ADD: `scripts/qa/engine_scenarios.py <root>` and `scripts/qa/ui_smoke.py <root> <outdir>` (need Playwright; UI smoke answers /api from the real Store, so it works without FastAPI).
**Known / unchanged**: finance parity divergence PARITY-1 (see docs/ISSUES.md) awaits a business decision; nothing in the
finance rules was changed.
