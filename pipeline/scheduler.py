"""
JADWAL RUN OTOMATIS -- dijalankan thread di dalam worker.py (bukan web server).

Satu jadwal = waktu (jam dinding WIB; harian / mingguan / bulanan) + RENTANG tanggal data + cakupan job
(pola nama / modul / daftar job / irama data) + opsi run.
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
  - Rentang (window_mode): offset = satu tanggal (H-offset); last_days = N hari s/d H-offset (mis. H-2..H-1:
    H-1 baru + H-2 diambil ulang sebagai pemeriksaan/penyembuhan); prev_week = Senin-Minggu minggu lalu;
    prev_month = bulan kalender lalu. Rentang > 1 hari: job snapshot (master) tetap SEKALI dengan label
    tanggal AWAL rentang (run_store.plan_items) -- jadwalkan master terpisah.
"""

from __future__ import annotations

import fnmatch
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pipeline import run_store
from pipeline.job_store import JobRow, load_job_rows, utc_now, write_tx
from pipeline.job_templates import s3_target_info

WIB = ZoneInfo("Asia/Jakarta")
WEEKDAY_LABEL = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
LATE_NOTE_AFTER = timedelta(minutes=10)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def next_occurrence(frequency: str, time_of_day: str, weekdays: list[int] | None, after: datetime,
                    day_of_month: int | None = None) -> datetime:
    """Kejadian berikutnya SETELAH `after` (aware), dihitung di jam dinding WIB -> datetime UTC."""
    hh, mm = (int(x) for x in time_of_day.split(":"))
    local = after.astimezone(WIB)
    for add in range(0, 63):
        day = local.date() + timedelta(days=add)
        if frequency == "weekly" and day.weekday() not in (weekdays or []):
            continue
        if frequency == "monthly" and day.day != day_of_month:
            continue
        candidate = datetime.combine(day, time(hh, mm), WIB)
        if candidate > local:
            return candidate.astimezone(timezone.utc)
    raise ValueError("jadwal mingguan tanpa hari / bulanan tanpa tanggal yang valid")


def target_date_for(fire_at: datetime, date_offset: int) -> date:
    """Tanggal data = tanggal kalender WIB saat jadwal - offset hari (1 = kemarin)."""
    return fire_at.astimezone(WIB).date() - timedelta(days=date_offset)


def date_range_for(s: dict, fire_at: datetime) -> tuple[date, date]:
    """Rentang tanggal data satu kejadian jadwal, dihitung dari tanggal jadwal (bukan jam tembak)."""
    mode = s.get("window_mode") or "offset"
    day = fire_at.astimezone(WIB).date()
    if mode == "prev_week":
        start = day - timedelta(days=day.weekday() + 7)
        return start, start + timedelta(days=6)
    if mode == "prev_month":
        end = day.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    end = target_date_for(fire_at, s["date_offset"])
    days = (s.get("window_days") or 1) if mode == "last_days" else 1
    return end - timedelta(days=days - 1), end


def describe_window(s: dict) -> str:
    mode = s.get("window_mode") or "offset"
    if mode == "prev_week":
        return "Minggu lalu (Senin-Minggu)"
    if mode == "prev_month":
        return "Bulan lalu"
    end = "hari ini" if s["date_offset"] == 0 else f"H-{s['date_offset']}"
    if mode == "last_days" and (s.get("window_days") or 1) > 1:
        return f"H-{s['date_offset'] + s['window_days'] - 1} s/d {end} ({s['window_days']} hari)"
    return {0: "Hari ini (H-0)", 1: "Kemarin (H-1)"}.get(s["date_offset"], end)


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
    if mode == "cadence":
        wanted = set(selector.get("cadences", []))
        return [r for r in rows if r.effective_cadence in wanted]
    return []


def describe(s: dict) -> str:
    if s["frequency"] == "daily":
        when = "Setiap hari"
    elif s["frequency"] == "monthly":
        when = f"Setiap tanggal {s['day_of_month']}"
    else:
        when = "Setiap " + ", ".join(WEEKDAY_LABEL[d] for d in sorted(s["weekdays"] or []))
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
    nxt = _iso(next_occurrence(data["frequency"], data["time_of_day"], data["weekdays"], now,
                               data.get("day_of_month"))) if data["enabled"] else None
    values = (data["name"], int(data["enabled"]), data["frequency"], data["time_of_day"], json.dumps(data["weekdays"]),
              data.get("day_of_month"), data["date_offset"], data.get("window_mode") or "offset",
              data.get("window_days") or 1, json.dumps(data["selector"]), json.dumps(data["options"]), nxt)
    if sid is None:
        cur = conn.execute(
            "INSERT INTO schedules (name, enabled, frequency, time_of_day, weekdays, day_of_month, date_offset, "
            "window_mode, window_days, selector, options, next_run_at, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, by, utc_now()))
        return cur.lastrowid
    conn.execute("UPDATE schedules SET name = ?, enabled = ?, frequency = ?, time_of_day = ?, weekdays = ?, "
                 "day_of_month = ?, date_offset = ?, window_mode = ?, window_days = ?, selector = ?, options = ?, "
                 "next_run_at = ?, updated_by = ?, updated_at = ? WHERE id = ?", (*values, by, utc_now(), sid))
    return sid


def set_enabled(conn: sqlite3.Connection, sid: int, enabled: bool, by: str) -> None:
    s = get_schedule(conn, sid)
    nxt = _iso(next_occurrence(s["frequency"], s["time_of_day"], s["weekdays"], datetime.now(timezone.utc),
                               s["day_of_month"])) if enabled else None
    conn.execute("UPDATE schedules SET enabled = ?, next_run_at = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                 (int(enabled), nxt, by, utc_now(), sid))


def fire(conn: sqlite3.Connection, s: dict, fire_at: datetime, note: str = "") -> int | None:
    """Buat run untuk satu kejadian jadwal. Return id run, atau None kalau tidak ada job yang cocok."""
    jobs = resolve_selector(s["selector"], load_job_rows())
    start, end = date_range_for(s, fire_at)
    if not jobs:
        conn.execute("UPDATE schedules SET last_run_at = ?, last_message = ? WHERE id = ?",
                     (utc_now(), f"Tidak ada job aktif yang cocok — run tidak dibuat.{note}", s["id"]))
        return None
    items = run_store.plan_items(jobs, start, end)
    run_id = run_store.create_run(conn, f"jadwal:{s['name']}", start, end, s["options"], items, schedule_id=s["id"])
    dates = start.isoformat() if start == end else f"{start.isoformat()} s/d {end.isoformat()}"
    conn.execute("UPDATE schedules SET last_run_at = ?, last_run_id = ?, last_message = ? WHERE id = ?",
                 (utc_now(), run_id, f"Run #{run_id}: {len(jobs)} job, tanggal {dates}.{note}", s["id"]))
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
            nxt = next_occurrence(s["frequency"], s["time_of_day"], s["weekdays"], now, s["day_of_month"])
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
