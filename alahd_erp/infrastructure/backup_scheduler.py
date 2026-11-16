"""Settings-driven Backup Scheduler for Yawm Jadid Academy.

Replaces the hard-coded `backup_loop` in main.py (v1.2.x) with a configurable service that
reads its configuration live from the central settings registry:

- `autoBackupEnabled`            master switch (safe default: ON)
- `autoBackupIntervalMinutes`    cycle interval, 5..1440 (safe default: 30)
- `autoBackupKeep`               retention for `alahd_auto_*` files, 3..500 (safe default: 48)
- `dailyBackupKeep`              retention for `alahd_daily_*` files, 3..500 (safe default: 30)
- `backupOnExit`                 final backup when the application closes (safe default: ON)
- `verifyBackupsOnCreate`        re-verify the finalized file after the atomic swap (safe default: ON;
                                 the pre-swap verification inside `Store.backup_to` always runs and
                                 cannot be disabled)
- `mirrorBackupsToExtraFolder` + `extraBackupFolder`  secondary destination (verified atomic copy)

Design rules:
- A failure of any single backup (full disk, disconnected USB, locked folder) is isolated: it is
  logged, counted, and surfaced through `status()` — it never prevents the next cycle and never
  touches the live database.
- Retention never deletes the newest file, and the retention counts are clamped to >= 3 so a
  settings mistake can never wipe every recovery point.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .database import Store, rotate, verify_sqlite_backup_file

_LOG = logging.getLogger("alahd_erp.backup")

_MIN_KEEP = 3                       # never allow retention below this (settings validation mirrors it)
_FALLBACK_INTERVAL_MIN = 30         # used only when settings cannot be read at all


class BackupScheduler:
    """Runs periodic / daily / exit backups driven by the live settings of the given store."""

    def __init__(self, store: Store, app_home: Path) -> None:
        self.store = store
        self.app_home = Path(app_home)
        self.backups_dir = self.app_home / "backups"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_rev: int = -1
        self._status: dict[str, Any] = {
            "startedAt": None,
            "lastAutoBackupAt": None,
            "lastDailyBackupAt": None,
            "lastExitBackupAt": None,
            "lastError": "",
            "consecutiveFailures": 0,
            "totalBackups": 0,
            "running": False,
        }

    # ------------------------------------------------------------------ settings
    def _settings(self) -> dict[str, Any]:
        try:
            return self.store.get_settings()
        except Exception:
            _LOG.exception("backup scheduler could not read settings; using safe defaults")
            return {}

    def _enabled(self, s: dict[str, Any]) -> bool:
        # Missing/garbled value => default ON (never silently disable essential data protection).
        return bool(s.get("autoBackupEnabled", True))

    def _interval_seconds(self, s: dict[str, Any]) -> float:
        try:
            minutes = int(s.get("autoBackupIntervalMinutes") or _FALLBACK_INTERVAL_MIN)
        except (TypeError, ValueError):
            minutes = _FALLBACK_INTERVAL_MIN
        return float(max(1, minutes) * 60)

    def _keep(self, s: dict[str, Any], key: str, default: int) -> int:
        try:
            k = int(s.get(key) or default)
        except (TypeError, ValueError):
            k = default
        return max(_MIN_KEEP, k)

    # ------------------------------------------------------------------ actions
    def run_auto_backup(self, reason: str = "periodic", force: bool = False) -> dict[str, Any]:
        """Creates one `alahd_auto_*` backup (skipped when the database has not changed since the last one unless forced)."""
        with self._lock:
            if not force and self.store.rev == self._last_rev:
                return {"ran": False, "reason": "unchanged", "rev": self.store.rev}
            ts = time.strftime('%Y%m%d_%H%M%S')
            dest = self.backups_dir / f"alahd_auto_{ts}.sqlite3"
            idx = 1
            while dest.exists():
                dest = self.backups_dir / f"alahd_auto_{ts}_{idx:02d}.sqlite3"
                idx += 1
            try:
                self.store.backup_to(dest, mirror_to_extra=True)
                self._post_verify(dest)
                rotate(self.backups_dir, "alahd_auto_*.sqlite3", self._keep(self._settings(), "autoBackupKeep", 48))
                self._last_rev = self.store.rev
                self._mark("lastAutoBackupAt")
                return {"ran": True, "file": dest.name, "rev": self.store.rev, "reason": reason}
            except Exception as exc:
                self._fail(exc)
                return {"ran": False, "error": str(exc), "reason": reason}

    def run_daily_backup(self, force: bool = False) -> dict[str, Any]:
        """Creates the single `alahd_daily_YYYYMMDD` recovery point for today (if not already present)."""
        daily = self.backups_dir / f"alahd_daily_{time.strftime('%Y%m%d')}.sqlite3"
        if daily.exists() and not force:
            return {"ran": False, "reason": "already_exists", "file": daily.name}
        try:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            self.store.backup_to(daily, mirror_to_extra=True)
            self._post_verify(daily)
            rotate(self.backups_dir, "alahd_daily_*.sqlite3", self._keep(self._settings(), "dailyBackupKeep", 30))
            self._mark("lastDailyBackupAt")
            return {"ran": True, "file": daily.name}
        except Exception as exc:
            self._fail(exc)
            return {"ran": False, "error": str(exc)}

    def run_exit_backup(self) -> dict[str, Any]:
        """Final backup on application close (respects `backupOnExit`, runs even if periodic is disabled)."""
        s = self._settings()
        if not bool(s.get("backupOnExit", True)):
            return {"ran": False, "reason": "disabled"}
        dest = self.backups_dir / f"alahd_auto_{time.strftime('%Y%m%d_%H%M%S')}_exit.sqlite3"
        try:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            self.store.backup_to(dest, mirror_to_extra=True)
            self._post_verify(dest)
            rotate(self.backups_dir, "alahd_auto_*.sqlite3", self._keep(s, "autoBackupKeep", 48))
            self._mark("lastExitBackupAt")
            return {"ran": True, "file": dest.name}
        except Exception as exc:
            self._fail(exc)
            return {"ran": False, "error": str(exc)}

    def _post_verify(self, dest: Path) -> None:
        """Re-verification of the finalized file (the pre-swap check inside backup_to always runs)."""
        if not self._settings().get("verifyBackupsOnCreate", True):
            return
        if not verify_sqlite_backup_file(dest):
            raise ValueError(f"post-write verification failed for {dest.name}")

    def _mark(self, key: str) -> None:
        self._status[key] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._status["lastError"] = ""
        self._status["consecutiveFailures"] = 0
        self._status["totalBackups"] = int(self._status["totalBackups"]) + 1

    def _fail(self, exc: Exception) -> None:
        self._status["lastError"] = str(exc)
        self._status["consecutiveFailures"] = int(self._status["consecutiveFailures"]) + 1
        _LOG.warning("backup failed (%s consecutive): %s", self._status["consecutiveFailures"], exc)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self._status["running"] = True
        self._status["startedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alahd-backup-scheduler")
        self._thread.start()

    def stop(self, run_exit_backup: bool = True) -> dict[str, Any]:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        self._status["running"] = False
        result: dict[str, Any] = {"stopped": True}
        if run_exit_backup:
            result["exitBackup"] = self.run_exit_backup()
        return result

    def _loop(self) -> None:
        # First cycle: catch up on today's daily recovery point, then backup if data changed.
        try:
            self.run_daily_backup()
            self.run_auto_backup(reason="startup")
        except Exception:
            _LOG.exception("first backup cycle failed (isolated; scheduler continues)")
        while not self._stop.is_set():
            interval = self._interval_seconds(self._settings())
            if self._stop.wait(interval):
                break
            try:
                if self._enabled(self._settings()):
                    self.run_daily_backup()
                    self.run_auto_backup(reason="periodic")
            except Exception:
                _LOG.exception("backup cycle failed (isolated; scheduler continues)")

    def status(self) -> dict[str, Any]:
        s = self._settings()
        out = dict(self._status)
        out["config"] = {
            "autoBackupEnabled": self._enabled(s),
            "intervalMinutes": int(self._interval_seconds(s) // 60),
            "autoBackupKeep": self._keep(s, "autoBackupKeep", 48),
            "dailyBackupKeep": self._keep(s, "dailyBackupKeep", 30),
            "backupOnExit": bool(s.get("backupOnExit", True)),
            "verifyBackupsOnCreate": bool(s.get("verifyBackupsOnCreate", True)),
            "mirrorToExtraFolder": bool(s.get("mirrorBackupsToExtraFolder", True)),
            "extraBackupFolder": str(s.get("extraBackupFolder") or ""),
        }
        out["backupsDir"] = str(self.backups_dir)
        return out
