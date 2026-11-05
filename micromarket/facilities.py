"""Cleaning tracker + facilities (equipment and issue) tracker.

Cleaning: recurring tasks, each with a frequency; status is derived from the last log.
Facilities: an equipment register and issue tickets. An asset may be linked to an EV charger,
so a critical issue takes that charger out of service and resolving it brings it back.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .charging import Charging
from .store import Store, StoreError

FMT = "%Y-%m-%d %H:%M:%S"
SEVERITIES = ("low", "normal", "high", "critical")
ISSUE_STATUSES = ("open", "in_progress", "resolved")
DUE_SOON_FRACTION = 0.25   # "due soon" = last 25% of the interval remains


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(t: str | None, default: datetime) -> datetime:
    return datetime.strptime(t, FMT) if t else default


class Facilities:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.charging = Charging(store)

    # ---- cleaning ------------------------------------------------------------
    def add_task(self, name: str, frequency_hours: float, area: str = "general",
                 instructions: str | None = None, actor: str = "staff", now: str | None = None) -> dict:
        name = name.strip()
        if not name or frequency_hours <= 0:
            raise StoreError("A cleaning task needs a name and a positive frequency")
        try:
            with self.store._tx():
                tid = self.db.execute(
                    "INSERT INTO cleaning_tasks(name,area,frequency_hours,instructions,created_at) VALUES (?,?,?,?,?)",
                    (name, area.strip().lower() or "general", frequency_hours, instructions,
                     now or _now().strftime(FMT))).lastrowid
                self.store._audit(actor, "add_cleaning_task", f"{name} every {frequency_hours}h")
        except Exception as e:
            if "UNIQUE" in str(e):
                raise StoreError(f"Cleaning task already exists: {name}")
            raise
        return self._task(tid)

    def _task(self, tid: int) -> dict:
        r = self.db.execute("SELECT * FROM cleaning_tasks WHERE id=?", (tid,)).fetchone()
        if not r:
            raise StoreError(f"Unknown cleaning task: {tid}")
        return dict(r)

    def set_task_active(self, tid: int, active: bool, actor: str = "staff"):
        self._task(tid)
        with self.store._tx():
            self.db.execute("UPDATE cleaning_tasks SET active=? WHERE id=?", (int(active), tid))
            self.store._audit(actor, "cleaning_task_active", f"#{tid} active={active}")

    def log_cleaning(self, task_id: int, done_by: str, notes: str | None = None,
                     now: str | None = None, actor: str = "staff") -> dict:
        done_by = done_by.strip()
        if not done_by:
            raise StoreError("Say who did the cleaning")
        t = self._task(task_id)
        if not t["active"]:
            raise StoreError("That cleaning task is switched off")
        ts = now or _now().strftime(FMT)
        with self.store._tx():
            lid = self.db.execute("INSERT INTO cleaning_logs(task_id,ts,done_by,notes) VALUES (?,?,?,?)",
                                  (task_id, ts, done_by, notes)).lastrowid
            self.store._audit(actor, "cleaning_done", f"{t['name']} by {done_by}")
        return dict(self.db.execute("SELECT * FROM cleaning_logs WHERE id=?", (lid,)).fetchone())

    def list_cleaning(self, now: str | None = None) -> list[dict]:
        """Active tasks with derived status: ok | due_soon | overdue (never done counts as overdue)."""
        t = _parse(now, _now())
        out = []
        for r in self.db.execute("SELECT * FROM cleaning_tasks WHERE active=1 ORDER BY area, name"):
            last = self.db.execute("SELECT ts, done_by, notes FROM cleaning_logs WHERE task_id=?"
                                   " ORDER BY ts DESC, id DESC LIMIT 1", (r["id"],)).fetchone()
            freq = timedelta(hours=r["frequency_hours"])
            if last:
                due = datetime.strptime(last["ts"], FMT) + freq
                remaining = due - t
                status = ("overdue" if remaining <= timedelta(0)
                          else "due_soon" if remaining <= freq * DUE_SOON_FRACTION else "ok")
            else:
                due, remaining, status = None, None, "overdue"
            out.append({**dict(r), "last_done": last["ts"] if last else None,
                        "last_by": last["done_by"] if last else None,
                        "due_at": due.strftime(FMT) if due else None, "status": status,
                        "hours_overdue": round(-remaining.total_seconds() / 3600, 1)
                        if remaining is not None and remaining <= timedelta(0) else 0})
        order = {"overdue": 0, "due_soon": 1, "ok": 2}
        out.sort(key=lambda x: (order[x["status"]], -x["hours_overdue"], x["name"]))
        return out

    def cleaning_history(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT l.*, t.name task, t.area FROM cleaning_logs l JOIN cleaning_tasks t ON t.id=l.task_id"
            " ORDER BY l.ts DESC, l.id DESC LIMIT ?", (limit,))]

    def compliance(self, days: int = 14, now: str | None = None) -> dict:
        """Share of expected cleans actually logged (each task capped at 100%).

        Only the time a task has existed counts, and a task isn't measured until a full interval has
        passed since it was created, so a brand-new setup doesn't start out looking like a failure.
        """
        t = _parse(now, _now())
        since = t - timedelta(days=days)
        per, total_expected, total_done = [], 0.0, 0.0
        for r in self.db.execute("SELECT * FROM cleaning_tasks WHERE active=1"):
            start = max(since, datetime.strptime(r["created_at"], FMT))
            expected = (t - start).total_seconds() / 3600 / r["frequency_hours"]
            if expected < 1:
                continue
            n = self.db.execute("SELECT COUNT(*) FROM cleaning_logs WHERE task_id=? AND ts>=? AND ts<=?",
                                (r["id"], start.strftime(FMT), t.strftime(FMT))).fetchone()[0]
            per.append({"task": r["name"], "done": n, "expected": round(expected, 1),
                        "rate": round(min(n / expected, 1.0), 2)})
            total_expected += expected
            total_done += min(n, expected)
        return {"days": days, "overall": round(total_done / total_expected, 2) if total_expected else None,
                "tasks": sorted(per, key=lambda x: x["rate"])}

    # ---- assets & issues -------------------------------------------------------
    def add_asset(self, name: str, kind: str = "equipment", location: str = "market",
                  charger_id: int | None = None, notes: str | None = None, actor: str = "staff") -> dict:
        name = name.strip()
        if not name:
            raise StoreError("An asset needs a name")
        if charger_id is not None and not self.db.execute("SELECT 1 FROM chargers WHERE id=?", (charger_id,)).fetchone():
            raise StoreError(f"Unknown charger: {charger_id}")
        try:
            with self.store._tx():
                aid = self.db.execute("INSERT INTO assets(name,kind,location,charger_id,notes) VALUES (?,?,?,?,?)",
                                      (name, kind.strip().lower(), location.strip().lower(), charger_id, notes)).lastrowid
                self.store._audit(actor, "add_asset", f"{name} ({kind}) @ {location}")
        except Exception as e:
            if "UNIQUE" in str(e):
                raise StoreError(f"Asset already exists: {name}")
            raise
        return self._asset(aid)

    def _asset(self, aid: int) -> dict:
        r = self.db.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        if not r:
            raise StoreError(f"Unknown asset: {aid}")
        return dict(r)

    def list_assets(self) -> list[dict]:
        out = []
        for a in self.db.execute("SELECT * FROM assets ORDER BY location, name"):
            n = self.db.execute("SELECT COUNT(*) FROM issues WHERE asset_id=? AND status!='resolved'",
                                (a["id"],)).fetchone()[0]
            out.append({**dict(a), "open_issues": n})
        return out

    def report_issue(self, title: str, description: str | None = None, severity: str = "normal",
                     asset_id: int | None = None, reported_by: str | None = None, source: str = "owner",
                     take_out_of_service: bool = False, actor: str = "staff", now: str | None = None) -> dict:
        title = title.strip()
        if not title:
            raise StoreError("Describe the problem")
        if severity not in SEVERITIES:
            raise StoreError(f"Severity must be one of {', '.join(SEVERITIES)}")
        asset = self._asset(asset_id) if asset_id is not None else None
        with self.store._tx():
            iid = self.db.execute(
                "INSERT INTO issues(asset_id,title,description,severity,reported_by,source,ts) VALUES (?,?,?,?,?,?,?)",
                (asset_id, title, description, severity, reported_by, source,
                 now or _now().strftime(FMT))).lastrowid
            self.store._audit(actor, "report_issue", f"#{iid} [{severity}] {title}")
        if asset:
            if take_out_of_service or severity == "critical":
                self._set_asset_status(asset, "out_of_service", actor)
            elif severity == "high" and asset["status"] == "ok":
                self._set_asset_status(asset, "needs_attention", actor)
        return self.get_issue(iid)

    def _set_asset_status(self, asset: dict, status: str, actor: str):
        with self.store._tx():
            self.db.execute("UPDATE assets SET status=? WHERE id=?", (status, asset["id"]))
            self.store._audit(actor, "asset_status", f"{asset['name']}: {asset['status']} -> {status}")
        if asset["charger_id"] is not None:   # keep the charging side in step
            self.charging.set_in_service(asset["charger_id"], status != "out_of_service", actor=actor)

    def update_issue(self, issue_id: int, status: str, resolution: str | None = None,
                     actor: str = "staff", now: str | None = None) -> dict:
        if status not in ISSUE_STATUSES:
            raise StoreError(f"Status must be one of {', '.join(ISSUE_STATUSES)}")
        iss = self.get_issue(issue_id)
        if iss["status"] == "resolved" and status != "resolved":
            raise StoreError("Resolved issues can't be reopened; report a new one")
        with self.store._tx():
            self.db.execute(
                "UPDATE issues SET status=?, resolution=COALESCE(?,resolution), resolved_at=? WHERE id=?",
                (status, resolution, (now or _now().strftime(FMT)) if status == "resolved" else None, issue_id))
            self.store._audit(actor, "update_issue", f"#{issue_id} -> {status}")
        if status == "resolved" and iss["asset_id"] is not None:
            left = self.db.execute("SELECT COUNT(*) FROM issues WHERE asset_id=? AND status!='resolved'",
                                   (iss["asset_id"],)).fetchone()[0]
            asset = self._asset(iss["asset_id"])
            if left == 0 and asset["status"] != "ok":
                self._set_asset_status(asset, "ok", actor)
        return self.get_issue(issue_id)

    def get_issue(self, iid: int) -> dict:
        r = self.db.execute("SELECT i.*, a.name asset FROM issues i LEFT JOIN assets a ON a.id=i.asset_id"
                            " WHERE i.id=?", (iid,)).fetchone()
        if not r:
            raise StoreError(f"Unknown issue: {iid}")
        return dict(r)

    def list_issues(self, include_resolved: bool = False, limit: int = 50) -> list[dict]:
        q = ("SELECT i.*, a.name asset FROM issues i LEFT JOIN assets a ON a.id=i.asset_id"
             + ("" if include_resolved else " WHERE i.status!='resolved'")
             + " ORDER BY CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,"
               " i.id DESC LIMIT ?")
        return [dict(r) for r in self.db.execute(q, (limit,))]

    # ---- summary ---------------------------------------------------------------
    def summary(self, now: str | None = None) -> dict:
        cleaning = self.list_cleaning(now)
        issues = self.list_issues()
        assets = self.list_assets()
        return {"cleaning": cleaning, "overdue": sum(c["status"] == "overdue" for c in cleaning),
                "due_soon": sum(c["status"] == "due_soon" for c in cleaning),
                "compliance": self.compliance(14, now), "issues": issues, "open_issues": len(issues),
                "critical_issues": sum(i["severity"] == "critical" for i in issues),
                "assets": assets, "assets_down": sum(a["status"] == "out_of_service" for a in assets)}
