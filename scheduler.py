"""
JADWAL RUN OTOMATIS -- dijalankan thread di dalam worker.py (bukan web server).

Satu jadwal = waktu (jam dinding WIB) + cakupan job (pola nama / modul / daftar job) + opsi run.
Saat jatuh tempo, scheduler MEMBUAT RUN BIASA (masuk antrian worker): run terjadwal tercatat di
Riwayat Run seperti run manual, dan aturan "satu job tidak pernah jalan dobel" tetap berlaku.

Aturan penting:
  - Tanggal data dihitung dari WAKTU JADWAL (bukan jam scheduler kebetulan menembak) -- run yang
    terlambat (worker mati) tetap memproses tanggal yang benar.
  - Kalau beberapa kejadian terlewat (worker mati berhari-hari), hanya SATU yang ditembak (yang
    terakhir); sisanya dicatat di last_message -- backfill manual lewat halaman Jalankan.
  - Klaim jadwal (geser next_run_at) dalam BEGIN IMMEDIATE: dua worker tidak bisa menembak
    kejadian yang sama.
  - Cakupan dievaluasi SAAT DITEMBAK -> job baru yang cocok pola otomatis ikut.
"""

from __future__ import annotations

import fnmatch
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import run_store
from job_store import JobRow, load_job_rows, utc_now, write_tx
from job_templates import s3_target_info

WIB = ZoneInfo("Asia/Jakarta")
WEEKDAY_LABEL = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
LATE_NOTE_AFTER = timedelta(minutes=10)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def next_occurrence(frequency: str, time_of_day: str, weekdays: list[int] | None, after: datetime) -> datetime:
    """Kejadian berikutnya SETELAH `after` (aware), dihitung di jam dinding WIB -> datetime UTC."""
    hh, mm = (int(x) for x in time_of_day.split(":"))
    local = after.astimezone(WIB)
    for add in range(0, 8):
        day = local.date() + timedelta(days=add)
        if frequency == "weekly" and day.weekday() not in (weekdays or []):
            continue
        candidate = datetime.combine(day, time(hh, mm), WIB)
        if candidate > local:
            return candidate.astimezone(timezone.utc)
    raise ValueError("jadwal mingguan tanpa hari yang dipilih")


def target_date_for(fire_at: datetime, date_offset: int) -> date:
    """Tanggal data = tanggal kalender WIB saat jadwal - offset hari (1 = kemarin)."""
    return fire_at.astimezone(WIB).date() - timedelta(days=date_offset)


def resolve_selector(selector: dict, rows: list[JobRow]) -> list[JobRow]:
    """Job AKTIF yang tercakup jadwal, urut sesuai urutan eksekusi."""
    mode = selector.get("mode")
    if mode == "pattern":
        pats = [p for p in selector.get("patterns", []) if p]
        return [r for r in rows if any(fnmatch.fnmatch(r.name, p) for p in pats)]
    if mode == "modules":
        mods = {m.lower() for m in selector.get("modules", [])}
        return [r for r in rows if ((s3_target_info(r.template, r.args) or {}).get("module") or "").lower() in mods]
    if mode == "jobs":
        names = set(selector.get("jobs", []))
        return [r for r in rows if r.name in names]
    return []


def describe(s: dict) -> str:
    when = "Setiap hari" if s["frequency"] == "daily" else \
        "Setiap " + ", ".join(WEEKDAY_LABEL[d] for d in sorted(s["weekdays"] or []))
    return f"{when} {s['time_of_day']} WIB"


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    d["weekdays"] = json.loads(d["weekdays"]) if d["weekdays"] else []
    d["selector"] = json.loads(d["selector"])
    d["options"] = json.loads(d["options"])
    return d


def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT s.*, r.status AS last_run_status FROM schedules s "
                        "LEFT JOIN runs r ON r.id = s.last_run_id ORDER BY s.name").fetchall()
    return [_row(r) for r in rows]


def get_schedule(conn: sqlite3.Connection, sid: int) -> dict | None:
    r = conn.execute("SELECT s.*, r.status AS last_run_status FROM schedules s "
                     "LEFT JOIN runs r ON r.id = s.last_run_id WHERE s.id = ?", (sid,)).fetchone()
    return _row(r) if r else None


def save_schedule(conn: sqlite3.Connection, data: dict, by: str, sid: int | None = None) -> int:
    """Buat / ubah jadwal. next_run_at dihitung ulang dari sekarang (NULL kalau nonaktif)."""
    now = datetime.now(timezone.utc)
    nxt = _iso(next_occurrence(data["frequency"], data["time_of_day"], data["weekdays"], now)) if data["enabled"] else None
    values = (data["name"], int(data["enabled"]), data["frequency"], data["time_of_day"], json.dumps(data["weekdays"]),
              data["date_offset"], json.dumps(data["selector"]), json.dumps(data["options"]), nxt)
    if sid is None:
        cur = conn.execute(
            "INSERT INTO schedules (name, enabled, frequency, time_of_day, weekdays, date_offset, selector, options, "
            "next_run_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, by, utc_now()))
        return cur.lastrowid
    conn.execute("UPDATE schedules SET name = ?, enabled = ?, frequency = ?, time_of_day = ?, weekdays = ?, "
                 "date_offset = ?, selector = ?, options = ?, next_run_at = ?, updated_by = ?, updated_at = ? "
                 "WHERE id = ?", (*values, by, utc_now(), sid))
    return sid


def set_enabled(conn: sqlite3.Connection, sid: int, enabled: bool, by: str) -> None:
    s = get_schedule(conn, sid)
    nxt = _iso(next_occurrence(s["frequency"], s["time_of_day"], s["weekdays"], datetime.now(timezone.utc))) \
        if enabled else None
    conn.execute("UPDATE schedules SET enabled = ?, next_run_at = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                 (int(enabled), nxt, by, utc_now(), sid))


def fire(conn: sqlite3.Connection, s: dict, fire_at: datetime, note: str = "") -> int | None:
    """Buat run untuk satu kejadian jadwal. Return id run, atau None kalau tidak ada job yang cocok."""
    jobs = resolve_selector(s["selector"], load_job_rows())
    d = target_date_for(fire_at, s["date_offset"])
    if not jobs:
        conn.execute("UPDATE schedules SET last_run_at = ?, last_message = ? WHERE id = ?",
                     (utc_now(), f"Tidak ada job aktif yang cocok — run tidak dibuat.{note}", s["id"]))
        return None
    items = run_store.plan_items(jobs, d, d)
    run_id = run_store.create_run(conn, f"jadwal:{s['name']}", d, d, s["options"], items, schedule_id=s["id"])
    conn.execute("UPDATE schedules SET last_run_at = ?, last_run_id = ?, last_message = ? WHERE id = ?",
                 (utc_now(), run_id, f"Run #{run_id}: {len(jobs)} job, tanggal {d.isoformat()}.{note}", s["id"]))
    return run_id


def tick(conn: sqlite3.Connection, now: datetime | None = None) -> list[int]:
    """Tembak semua jadwal yang jatuh tempo. Dipanggil berkala oleh worker. Return id run yang dibuat."""
    now = now or datetime.now(timezone.utc)
    due = []
    with write_tx(conn):
        for r in conn.execute("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NOT NULL "
                              "AND next_run_at <= ?", (_iso(now),)).fetchall():
            s = _row(r)
            scheduled = _parse(s["next_run_at"])
            nxt = next_occurrence(s["frequency"], s["time_of_day"], s["weekdays"], now)
            conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (_iso(nxt), s["id"]))
            due.append((s, scheduled))
    created = []
    for s, scheduled in due:
        late = now - scheduled
        note = f" Terlambat {int(late.total_seconds() // 60)} menit (worker mati saat jadwal?)." if late > LATE_NOTE_AFTER else ""
        run_id = fire(conn, s, scheduled, note)
        if run_id:
            created.append(run_id)
    return created
