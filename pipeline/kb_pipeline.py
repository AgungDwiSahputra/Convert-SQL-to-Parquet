"""
KB PIPELINE -- kesegaran TIGA TAHAP jalur dokumen (PRD v1.1 Fase 10, FR-10.4/10.5): rclone -> Glue cleansing -> ingestion KB.

Hanya MEMBACA S3 lewat LIST (butuh s3:ListBucket; tanpa s3:GetObject):
  1. raw   : objek terbaru di documents/raw/ -- INFORMASI saja. rclone hanya mengunggah file yang berubah, jadi raw yang lama tidak
             berubah bisa wajar; tidak pernah memicu peringatan (sinyal andal butuh heartbeat dari sisi rclone).
  2. Glue  : laporan run documents/.../v2/<dataset>/reports/runs/dt=YYYY-MM-DD/<YYYYMMDDTHHMMSS>-<id>.jsonl. Waktu di nama file =
             waktu SELESAI run (UTC). Laporan dibuat tiap run walau tanpa perubahan, jadi ini sinyal "Glue sudah jalan".
             Terlambat = belum ada laporan sesudah jadwal (kb.glue_run_time) saat batas (kb.glue_deadline) lewat.
  3. KB    : LastModified documents/.../v2/<dataset>/state/kb_changed_at/<dataset> = penanda "isi KB berubah" yang ditulis job
             Glue sesaat sebelum memulai ingestion. Tertunda = penanda lebih baru daripada semua ingestion dan sudah lebih
             lama dari kb.pending_minutes.

Atribusi asal ingestion: ingestion yang selesai-laporannya 0-30 detik sesudah ingestion mulai = dimulai job Glue (selisih nyata
9-10 detik pada 7 dari 7 kasus). Terjadwal bila waktu laporan jatuh di jendela [glue_run_time, glue_deadline) WIB, selain itu
"di luar jadwal". Tanpa laporan di dekatnya = di luar aplikasi (console/manual). Ini KESIMPULAN dari waktu -- hanya label, tidak ada
peringatan/perilaku yang bergantung padanya. Kepastian lewat job_id di state butuh s3:GetObject (irisan berikutnya).

Penilaian (glue_state/kb_state) fungsi murni dari waktu + fakta tersimpan, dihitung saat dibaca. Gagal baca (izin, format laporan
tak dikenal) = status 'unreadable', BUKAN 'segar' (PRD risiko R12). Kelak, saat run Glue dijalankan dari aplikasi ini (Fase 6/7),
status Glue dibaca langsung dari JobRun dan tebakan jadwal tidak diperlukan lagi.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from pipeline import aws, notify
from pipeline.app_settings import get_settings, set_settings
from pipeline.config import KB_DOCS_PREFIX, S3_BUCKET_NAME
from pipeline.job_store import write_tx
from pipeline.scheduler import WIB

log = logging.getLogger("parquet_exporter")

RAW_PREFIX = KB_DOCS_PREFIX + "raw/"
V2_ROOT = KB_DOCS_PREFIX + "knowledge-base-preparation/v2/"
REPORT_RE = re.compile(r"/reports/runs/dt=\d{4}-\d{2}-\d{2}/(\d{8}T\d{6})-[0-9A-Za-z]+\.jsonl$")
REPORT_DAYS = 3                  # hari UTC terakhir yang di-LIST (cukup meliput jadwal terakhir)
ATTRIBUTION_WINDOW_S = 30        # laporan harus selesai 0-30 dtk sesudah ingestion mulai
ATTRIBUTION_MIN_AGE_S = 120      # beri waktu laporan ditulis sebelum menyimpulkan "tidak ada laporan"
MARKER_SKEW_S = 30               # toleransi selisih jam antara LastModified S3 dan startedAt Bedrock
RAW_MAX_PAGES = 100              # pindai raw/ maksimal 100 halaman LIST (100 ribu objek), lalu ditandai partial


def hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _wib(dt: datetime | None) -> str:
    return dt.astimezone(WIB).strftime("%d %b %H:%M WIB") if dt else "-"


# ---------------------------------------------------------------------------
# Pembacaan S3 (hanya LIST)
# ---------------------------------------------------------------------------

def parse_report_end(key: str) -> datetime | None:
    """Key laporan Glue -> waktu SELESAI run (UTC), atau None bila bukan file laporan."""
    m = REPORT_RE.search(key)
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) if m else None


def _list(s3, prefix: str, max_pages: int | None = None):
    """Hasilkan (key, last_modified UTC, halaman_ke) untuk semua objek di bawah prefix."""
    for n, page in enumerate(s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET_NAME, Prefix=prefix), 1):
        for o in page.get("Contents", []):
            yield o["Key"], o["LastModified"].astimezone(timezone.utc), n
        if max_pages and n >= max_pages:
            return


def list_report_ends(s3, dataset: str, days: list[date]) -> tuple[list[datetime], int, datetime | None]:
    """
    ([waktu selesai laporan], jumlah objek terbaca, LastModified objek tak dikenali terbaru) untuk folder laporan tanggal-tanggal
    UTC itu. Objek tak dikenali yang LEBIH BARU daripada laporan valid terakhir = tanda format nama file berubah (lihat refresh).
    """
    ends, count, unparsed = [], 0, None
    for d in days:
        for key, modified, _n in _list(s3, f"{V2_ROOT}{dataset}/reports/runs/dt={d.isoformat()}/"):
            count += 1
            end = parse_report_end(key)
            if end:
                ends.append(end)
            elif unparsed is None or modified > unparsed:
                unparsed = modified
    return sorted(ends), count, unparsed


def marker_modified(s3, dataset: str) -> datetime | None:
    """LastModified terbaru di state/kb_changed_at/ dataset (None = belum pernah ada perubahan)."""
    times = [m for _k, m, _n in _list(s3, f"{V2_ROOT}{dataset}/state/kb_changed_at/")]
    return max(times) if times else None


def dataset_exists(s3, dataset: str) -> bool | None:
    """Folder v2/<dataset>/ ada? None bila tidak bisa dipastikan (LIST gagal)."""
    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=f"{V2_ROOT}{dataset}/", MaxKeys=1)
    except Exception:  # noqa: BLE001 -- izin/jaringan: jangan menghalangi pendaftaran
        return None
    return bool(resp.get("KeyCount") or resp.get("Contents"))


def scan_raw(s3) -> dict:
    """Objek terbaru & jumlah objek di documents/raw/ (informasi)."""
    latest, count, pages = None, 0, 0
    for _key, modified, n in _list(s3, RAW_PREFIX, RAW_MAX_PAGES):
        count += 1
        pages = n
        if latest is None or modified > latest:
            latest = modified
    return {"latest_at": _iso(latest), "objects": count, "partial": pages >= RAW_MAX_PAGES}


def refresh_raw(conn: sqlite3.Connection, s3, now: datetime | None = None, force: bool = False) -> dict | None:
    """Pindai raw/ bila sudah lewat kb.raw_scan_minutes (atau force). Hasil di settings kb.raw_status. None = belum waktunya."""
    now = now or datetime.now(timezone.utc)
    s = get_settings(conn, "kb.")
    last = s["kb.raw_status"]
    if not force and last and now - _parse(last["checked_at"]) < timedelta(minutes=s["kb.raw_scan_minutes"]):
        return None
    result = {"checked_at": _iso(now), "latest_at": None, "objects": 0, "partial": False, "error": None}
    try:
        result.update(scan_raw(s3))
    except Exception as e:  # noqa: BLE001
        result["error"] = aws.error_message(e)
    set_settings(conn, {"kb.raw_status": result}, "worker")
    return result


# ---------------------------------------------------------------------------
# Penilaian (fungsi murni)
# ---------------------------------------------------------------------------

def glue_state(latest_end: datetime | None, now: datetime, run_time: time, deadline: time, error: str | None = None) -> dict:
    """
    Status tahap Glue: ok | waiting | late | unreadable. Yang dinilai = jadwal TERAKHIR yang sudah mulai (hari ini bila jam
    sekarang >= run_time, selain itu kemarin): ok bila sudah ada laporan selesai sesudah jadwal itu; waiting bila belum tapi batas
    belum lewat; late bila batas lewat.
    """
    now_wib = now.astimezone(WIB)
    day = now_wib.date() if now_wib.time() >= run_time else now_wib.date() - timedelta(days=1)
    run_at = datetime.combine(day, run_time, tzinfo=WIB)
    deadline_at = datetime.combine(day, deadline, tzinfo=WIB)
    if error:
        status = "unreadable"
    elif latest_end and latest_end >= run_at:
        status = "ok"
    else:
        status = "late" if now >= deadline_at else "waiting"
    return {"status": status, "expected_run_at": _iso(run_at), "deadline_at": _iso(deadline_at), "last_report_at": _iso(latest_end)}


def kb_state(marker_at: datetime | None, ingestions: list[tuple[datetime, str]], now: datetime, pending_minutes: int,
             error: str | None = None) -> dict:
    """
    Status tahap KB: ok | running | waiting | pending | failed | none | unreadable. `ingestions` = [(started_at, status)].
    Ada ingestion yang mulai SESUDAH penanda perubahan = sudah diproses (ok / berjalan / gagal). Belum ada: waiting selama
    batas belum lewat, pending sesudahnya.
    """
    out = {"status": "none", "marker_at": _iso(marker_at), "pending_minutes": pending_minutes}
    if error:
        out["status"] = "unreadable"
    elif marker_at is None:
        out["status"] = "none"
    else:
        after = [st for started, st in ingestions if started >= marker_at - timedelta(seconds=MARKER_SKEW_S)]
        if any(st in ("STARTING", "IN_PROGRESS", "STOPPING") for st in after):
            out["status"] = "running"
        elif any(st == "COMPLETE" for st in after):
            out["status"] = "ok"
        elif after:
            out["status"] = "failed"
        else:
            out["status"] = "pending" if now - marker_at >= timedelta(minutes=pending_minutes) else "waiting"
    return out


def overall(glue: dict | None, kb: dict | None) -> str:
    """attention (perlu perhatian) | unknown (belum bisa dinilai) | ok."""
    if not glue or not kb:
        return "unknown"
    if glue["status"] in ("late",) or kb["status"] in ("pending", "failed"):
        return "attention"
    if "unreadable" in (glue["status"], kb["status"]):
        return "unknown"
    return "ok"


# ---------------------------------------------------------------------------
# Segarkan satu target
# ---------------------------------------------------------------------------

def _recent_ingestions(conn: sqlite3.Connection, target_id: int) -> list[tuple[datetime, str]]:
    return [(_parse(r["started_at"]), r["status"]) for r in conn.execute(
        "SELECT started_at, status FROM kb_ingestions WHERE target_id = ? AND started_at IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 30", (target_id,))]


def attribute(conn: sqlite3.Connection, target: sqlite3.Row, s3, now: datetime, run_time: time, deadline: time) -> int:
    """Isi origin 'glue' (+ glue_scheduled) untuk ingestion yang punya laporan Glue 0-30 dtk sesudahnya. Return jumlah dilabeli Glue."""
    rows = conn.execute("SELECT id, started_at FROM kb_ingestions WHERE target_id = ? AND origin = 'external' AND origin_checked = 0 "
                        "AND status IN ('COMPLETE', 'FAILED', 'STOPPED') AND started_at IS NOT NULL", (target["id"],)).fetchall()
    cache: dict[date, list[datetime]] = {}
    labeled = 0
    for r in rows:
        start = _parse(r["started_at"])
        if (now - start).total_seconds() < ATTRIBUTION_MIN_AGE_S:
            continue                                   # laporan mungkin belum ditulis -- cek lagi nanti
        ends: list[datetime] = []
        for k in (-1, 0, 1):
            d = start.date() + timedelta(days=k)
            if d not in cache:
                cache[d] = list_report_ends(s3, target["dataset"], [d])[0]
            ends += cache[d]
        match = [e for e in ends if 0 <= (e - start).total_seconds() <= ATTRIBUTION_WINDOW_S]
        if match:
            scheduled = int(run_time <= min(match).astimezone(WIB).time() < deadline)
            conn.execute("UPDATE kb_ingestions SET origin = 'glue', glue_scheduled = ?, origin_checked = 1 WHERE id = ?",
                         (scheduled, r["id"]))
            labeled += 1
        else:
            conn.execute("UPDATE kb_ingestions SET origin_checked = 1 WHERE id = ?", (r["id"],))
    return labeled


def refresh(conn: sqlite3.Connection, target: sqlite3.Row, s3, now: datetime | None = None) -> dict:
    """
    LIST laporan Glue + penanda perubahan satu target jalur dokumen, simpan fakta, atribusi ingestion, dan kirim email sekali per
    episode (Glue terlambat / ingestion tertunda). Pembacaan pertama target tidak mengirim email (kondisi lama bukan kejadian baru).
    Return {status: glue/kb/overall, alerts, labeled, error}. Tidak melempar error.
    """
    now = now or datetime.now(timezone.utc)
    tid, dataset = target["id"], target["dataset"]
    s = get_settings(conn, "kb.")
    run_time, deadline = hhmm(s["kb.glue_run_time"]), hhmm(s["kb.glue_deadline"])
    prev = conn.execute("SELECT * FROM kb_pipeline_status WHERE target_id = ?", (tid,)).fetchone()

    latest, n_objects, marker, error = (prev["report_latest_at"] and _parse(prev["report_latest_at"])) if prev else None, \
        prev["report_objects"] if prev else None, (prev["marker_at"] and _parse(prev["marker_at"])) if prev else None, None
    try:
        today = now.astimezone(timezone.utc).date()
        ends, n_objects, unparsed = list_report_ends(s3, dataset, [today - timedelta(days=i) for i in range(REPORT_DAYS)])
        latest = max(ends) if ends else None
        if unparsed and (latest is None or unparsed > latest + timedelta(seconds=60)):
            error = "Format nama file laporan Glue tidak dikenali (reports/runs/dt=.../<waktu>-<id>.jsonl)."
    except Exception as e:  # noqa: BLE001
        error = aws.error_message(e)
    try:
        marker = marker_modified(s3, dataset)
    except Exception as e:  # noqa: BLE001
        error = error or aws.error_message(e)

    first = prev is None
    glue = glue_state(latest, now, run_time, deadline, error)
    kb = kb_state(marker, _recent_ingestions(conn, tid), now, s["kb.pending_minutes"], error)
    alerted_glue = prev["glue_alerted_for"] if prev else None
    alerted_kb = prev["pending_alerted_for"] if prev else None
    problems: list[tuple] = []
    if glue["status"] == "late" and alerted_glue != glue["expected_run_at"]:
        if not first:
            problems.append(("Glue cleansing", "belum ada laporan run",
                             f"jadwal {run_time:%H:%M} WIB, batas {deadline:%H:%M} WIB; laporan terakhir {_wib(latest)}"))
        alerted_glue = glue["expected_run_at"]
    if kb["status"] == "pending" and alerted_kb != kb["marker_at"]:
        if not first:
            problems.append(("Ingestion KB", "tertunda", f"isi KB berubah {_wib(marker)}, belum ada ingestion sesudahnya "
                                                         f"(batas {s['kb.pending_minutes']} menit)"))
        alerted_kb = kb["marker_at"]

    with write_tx(conn):
        conn.execute(
            "INSERT INTO kb_pipeline_status (target_id, checked_at, report_latest_at, report_objects, marker_at, error, "
            "glue_alerted_for, pending_alerted_for) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(target_id) DO UPDATE SET "
            "checked_at = excluded.checked_at, report_latest_at = excluded.report_latest_at, "
            "report_objects = excluded.report_objects, marker_at = excluded.marker_at, error = excluded.error, "
            "glue_alerted_for = excluded.glue_alerted_for, pending_alerted_for = excluded.pending_alerted_for",
            (tid, _iso(now), _iso(latest), n_objects, _iso(marker), error, alerted_glue, alerted_kb))
    if problems:
        try:
            notify.notify_kb_stale(conn, dict(target), problems)
        except Exception:  # noqa: BLE001 -- email tidak boleh menggagalkan pemantauan
            log.exception("Notifikasi kesegaran KB %s gagal diproses.", target["name"])
    labeled = 0
    try:
        labeled = attribute(conn, target, s3, now, run_time, deadline)
    except Exception as e:  # noqa: BLE001 -- label hanya pelengkap; dicoba lagi siklus berikutnya
        log.warning("Atribusi ingestion %s tertunda: %s", target["name"], aws.error_message(e))
    return {"glue": glue["status"], "kb": kb["status"], "overall": overall(glue, kb), "alerts": len(problems),
            "labeled": labeled, "error": error}


def record_error(conn: sqlite3.Connection, target_id: int, message: str, now: datetime | None = None) -> None:
    """Catat kegagalan membuat klien S3 / pembacaan menyeluruh (fakta lama dipertahankan)."""
    now = _iso(now or datetime.now(timezone.utc))
    conn.execute("INSERT INTO kb_pipeline_status (target_id, checked_at, error) VALUES (?, ?, ?) "
                 "ON CONFLICT(target_id) DO UPDATE SET checked_at = excluded.checked_at, error = excluded.error",
                 (target_id, now, message))


# ---------------------------------------------------------------------------
# Tampilan
# ---------------------------------------------------------------------------

def view(conn: sqlite3.Connection, target: sqlite3.Row, settings: dict | None = None, now: datetime | None = None) -> dict | None:
    """Status tiga tahap satu target untuk API/UI; None untuk target non-jalur-dokumen."""
    if target["pipeline"] != "documents":
        return None
    now = now or datetime.now(timezone.utc)
    s = settings or get_settings(conn, "kb.")
    st = conn.execute("SELECT * FROM kb_pipeline_status WHERE target_id = ?", (target["id"],)).fetchone()
    base = {"checked_at": None, "error": None, "glue": None, "kb": None, "overall": "unknown",
            "schedule": {"run_time": s["kb.glue_run_time"], "deadline": s["kb.glue_deadline"], "pending_minutes": s["kb.pending_minutes"]}}
    if st is None:
        return base
    err = st["error"]
    glue = glue_state(_parse(st["report_latest_at"]), now, hhmm(s["kb.glue_run_time"]), hhmm(s["kb.glue_deadline"]), err)
    kb = kb_state(_parse(st["marker_at"]), _recent_ingestions(conn, target["id"]), now, s["kb.pending_minutes"], err)
    return base | {"checked_at": st["checked_at"], "error": err, "glue": glue, "kb": kb, "overall": overall(glue, kb)}
