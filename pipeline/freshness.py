"""
KESEGARAN DATA -- apakah tiap job aktif sudah memproses tanggal terbaru yang wajar untuk iramanya.

Tanggal dianggap SUDAH DIPROSES kalau di dq_results ada hasil yang tidak ditahan DQ dan (sudah
dipublikasikan ATAU memang 0 baris -- tidak ada yang perlu diupload), ATAU partisinya ada di S3 menurut
inventaris S3 (tabel s3_inventory, pipeline/s3_inventory.py -- menangkap upload di luar catatan pipeline:
sebelum fitur DQ ada, CLI dengan jobs.db lain). Run uji --skip-s3 yang berisi data tidak dihitung.

Tanggal yang diharapkan (hari ini WIB = T):
  harian / snapshot : T-1 (proses hari ini mengolah H-1)
  mingguan          : Minggu terakhir yang <= T-1 (minggu Senin-Minggu sudah ditutup & dijalankan Senin)
  bulanan           : akhir bulan terakhir yang <= T-3 (bulan lalu dijalankan sekitar tanggal 3)

CELAH (find_gaps): tanggal / minggu / bulan di TENGAH riwayat yang belum diproses, walaupun data terbaru
sudah ada (ujung yang belum diproses = status 'late', tidak dilaporkan dua kali). Hanya irama harian,
mingguan, bulanan -- snapshot (master) tidak dicek. Dimulai dari tanggal tertua yang diketahui per job
(dq_results atau S3) dan dibatasi jangkauan dq.gap_days / gap_weeks / gap_months. Hari 0 baris tidak membuat
file di S3, jadi celah = belum ter-load ATAU memang kosong; run ulang tanggal itu menutupnya.
Minggu = konvensi tabel minggu PZO (lihat pzo_week).

Penjaga harian (run_daily, dipanggil thread scheduler di worker): sekali per hari setelah jam cek
(default 06:00 WIB), kirim email daftar job terlambat / ada celah. Default NONAKTIF -- aktifkan di Pengaturan
setelah jadwal pindah dari Task Scheduler ke aplikasi (run Task Scheduler di mesin lain dengan
jobs.db lain tidak terlihat di sini).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from pipeline import notify
from pipeline.app_settings import get_settings, set_settings
from pipeline.job_store import JobRow, load_job_rows
from pipeline.scheduler import WIB

GAP_CADENCES = ("daily", "weekly", "monthly")    # snapshot (master) tidak dicek celah
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
         "November", "Desember"]
_BLN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
_PROCESSED = "dq_status != 'blocked' AND (published = 1 OR rows = 0)"


def pzo_week(d: date) -> tuple[int, int, int, date, date]:
    """
    Minggu kalender konvensi tabel minggu PZO (T_PZO_Week_Temp; cocok 100% untuk 2020-2028): Senin-Minggu,
    minggu lintas bulan ikut bulan hari KAMIS-nya, N = urutan minggu di bulan itu.
    Return (tahun, bulan, N, senin, minggu).
    """
    monday = d - timedelta(days=d.weekday())
    thursday = monday + timedelta(days=3)
    first = thursday.replace(day=1)
    first_thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return thursday.year, thursday.month, (thursday - first_thursday).days // 7 + 1, monday, monday + timedelta(days=6)


def pzo_week_start(year: int, month: int, n: int) -> date:
    """Kebalikan pzo_week: Senin minggu ke-N bulan itu. ValueError kalau bulan itu tidak punya minggu ke-N."""
    first = date(year, month, 1)
    monday = first + timedelta(days=(3 - first.weekday()) % 7 + 7 * (n - 1) - 3)
    if n < 1 or pzo_week(monday)[:3] != (year, month, n):
        raise ValueError(f"minggu ke-{n} {year}-{month:02d} tidak ada")
    return monday


def _fmt_range(a: date, b: date) -> str:
    if a == b:
        return f"{a.day} {_BLN[a.month - 1]} {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}-{b.day} {_BLN[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {_BLN[a.month - 1]} - {b.day} {_BLN[b.month - 1]} {a.year}"
    return f"{_fmt_range(a, a)} - {_fmt_range(b, b)}"


def gap_lookback(conn: sqlite3.Connection) -> dict:
    s = get_settings(conn, "dq.")
    return {"days": int(s["dq.gap_days"]), "weeks": int(s["dq.gap_weeks"]), "months": int(s["dq.gap_months"])}


def find_gaps(cadence: str, done: set[date], first: date | None, last: date | None, lookback: dict) -> list[dict]:
    """
    Periode di antara tanggal pertama & terakhir yang sudah diproses yang belum lengkap. Terbaru dulu:
    [{label, start, end, missing_days, total_days}].
    """
    if cadence not in GAP_CADENCES or not first or not last:
        return []
    out = []
    if cadence == "daily":
        day = max(first, last - timedelta(days=lookback["days"] - 1))
        run = []
        while day <= last:
            if day not in done:
                run.append(day)
            if run and (day in done or day == last):
                a, b = run[0], run[-1]
                out.append({"label": _fmt_range(a, b) + f" ({len(run)} hari)", "start": a.isoformat(),
                            "end": b.isoformat(), "missing_days": len(run), "total_days": len(run)})
                run = []
            day += timedelta(days=1)
    elif cadence == "weekly":
        monday = pzo_week(last)[3]
        if pzo_week(last)[4] > last:
            monday -= timedelta(days=7)          # minggu yang memuat `last` belum selesai
        for _ in range(lookback["weeks"]):
            if monday < first:
                break
            days = [monday + timedelta(days=i) for i in range(7)]
            missing = [x for x in days if x not in done]
            if missing:
                y, m, n, mon, sun = pzo_week(monday)
                out.append({"label": f"{BULAN[m - 1]} {y} minggu ke-{n} ({_fmt_range(mon, sun)}): "
                                     f"{len(missing)} dari 7 hari", "start": mon.isoformat(), "end": sun.isoformat(),
                            "missing_days": len(missing), "total_days": 7})
            monday -= timedelta(days=7)
    else:
        month_end = (last + timedelta(days=1)).replace(day=1) - timedelta(days=1)
        if month_end > last:
            month_end = last.replace(day=1) - timedelta(days=1)   # bulan yang memuat `last` belum selesai
        for _ in range(lookback["months"]):
            start = month_end.replace(day=1)
            if start < first:
                break
            total = month_end.day
            missing = [start + timedelta(days=i) for i in range(total) if start + timedelta(days=i) not in done]
            if missing:
                out.append({"label": f"{BULAN[start.month - 1]} {start.year}: {len(missing)} dari {total} hari",
                            "start": start.isoformat(), "end": month_end.isoformat(),
                            "missing_days": len(missing), "total_days": total})
            month_end = start - timedelta(days=1)
    return sorted(out, key=lambda g: g["start"], reverse=True)


def expected_date(cadence: str, today: date) -> date:
    if cadence == "weekly":
        ref = today - timedelta(days=1)
        return ref - timedelta(days=(ref.weekday() + 1) % 7)
    if cadence == "monthly":
        ref = today - timedelta(days=3)
        return (ref + timedelta(days=1)).replace(day=1) - timedelta(days=1)
    return today - timedelta(days=1)


def latest_processed(conn: sqlite3.Connection) -> dict[str, str]:
    """job -> tanggal terbaru yang sudah diproses (lihat docstring modul)."""
    return dict(conn.execute(f"SELECT job_name, MAX(target_date) FROM dq_results WHERE {_PROCESSED} "
                             "GROUP BY job_name").fetchall())


def job_freshness(conn: sqlite3.Connection, rows: list[JobRow], today: date | None = None,
                  lookback: dict | None = None) -> dict[str, dict]:
    """job -> {status: fresh|late|unknown, cadence, latest, expected, lag_days, gaps}."""
    today = today or datetime.now(WIB).date()
    lookback = lookback or gap_lookback(conn)
    known = (f"SELECT job_name, target_date FROM dq_results WHERE {_PROCESSED} "
             "UNION SELECT job_name, target_date FROM s3_inventory")
    span = {n: (first, last) for n, first, last in conn.execute(
        f"SELECT job_name, MIN(target_date), MAX(target_date) FROM ({known}) GROUP BY job_name")}
    since = today - timedelta(days=max(lookback["days"], lookback["weeks"] * 7, lookback["months"] * 31) + 31)
    done: dict[str, set[date]] = {}
    for n, d in conn.execute(f"SELECT job_name, target_date FROM ({known}) WHERE target_date >= ?", (since.isoformat(),)):
        done.setdefault(n, set()).add(date.fromisoformat(d))
    out = {}
    for r in rows:
        cadence = r.effective_cadence
        expected = expected_date(cadence, today)
        first, last = span.get(r.name, (None, None))
        if last is None:
            status, lag = "unknown", None
        else:
            lag = (expected - date.fromisoformat(last)).days
            status = "fresh" if lag <= 0 else "late"
        gaps = None
        if cadence in GAP_CADENCES:
            items = find_gaps(cadence, done.get(r.name, set()), date.fromisoformat(first) if first else None,
                              date.fromisoformat(last) if last else None, lookback)
            gaps = {"count": len(items), "items": items[:12]}
        out[r.name] = {"status": status, "cadence": cadence, "latest": last, "expected": expected.isoformat(),
                       "lag_days": max(lag, 0) if lag is not None else None, "gaps": gaps}
    return out


def run_daily(conn: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    """Penjaga harian. Return ringkasan kalau berjalan putaran ini, None kalau belum waktunya / sudah / nonaktif."""
    s = get_settings(conn, "freshness.")
    if not s["freshness.enabled"]:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(WIB)
    hh, mm = (int(x) for x in s["freshness.check_time"].split(":"))
    if now.time() < time(hh, mm) or s["freshness.last_date"] == now.date().isoformat():
        return None
    set_settings(conn, {"freshness.last_date": now.date().isoformat()}, "worker")  # tandai dulu: gagal pun tidak diulang
    rows = load_job_rows()   # job aktif saja
    fresh = job_freshness(conn, rows, now.date())
    late = [{"job_name": n, **{k: v for k, v in f.items() if k != "gaps"}}
            for n, f in fresh.items() if f["status"] != "fresh"]
    gaps = [{"job_name": n, "cadence": f["cadence"], "count": f["gaps"]["count"],
             "items": [g["label"] for g in f["gaps"]["items"]]}
            for n, f in fresh.items() if f["gaps"] and f["gaps"]["count"]]
    result = {"date": now.date().isoformat(), "checked": len(rows), "late": late, "gaps": gaps}
    set_settings(conn, {"freshness.last_result": result}, "worker")
    if late or gaps:
        notify.notify_stale(conn, result)
    return result
