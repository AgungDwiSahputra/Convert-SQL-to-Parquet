"""
DATA QUALITY BRONZE -- pemeriksaan per dataset SETELAH Parquet ditulis dan SEBELUM upload ke S3
(pola write-audit-publish). Dipanggil export.run_job(), jadi berlaku sama untuk CLI, worker, jadwal.

  rows        : COUNT sumber (job.count_query, filter identik) vs baris tertulis.
                Periode tutup (job harian, tanggal <= kemarin WIB) -> selisih = DITAHAN;
                master / tanggal yang masih berjalan -> selisih = peringatan (data sumber masih berubah).
  schema      : kolom & tipe vs skema terakhir yang DIPUBLIKASIKAN job ini (jobs.schema_json).
                Kolom hilang / tipe berubah = DITAHAN; kolom baru = peringatan.
  date_range  : min/max kolom tanggal hasil harus di dalam target_date (job.date_check) -- filter
                salah format = DITAHAN.

Satu saja 'blocked' -> DQBlocked: upload ditahan, partisi lama di S3 tetap utuh, item dicatat gagal.
Perubahan skema yang disengaja: `python manage.py accept-schema <job>` lalu jalankan ulang.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.compute as pc

from pipeline.job_store import default_target_date

OK, WARNING, BLOCKED, UNCHECKED = "ok", "warning", "blocked", "unchecked"
_RANK = {UNCHECKED: 0, OK: 1, WARNING: 2, BLOCKED: 3}
AUDIT_COLUMNS = {"_ingested_at"}     # kolom teknis pipeline, bukan dari sumber -- tidak ikut skema acuan


@dataclass
class Check:
    name: str        # rows | schema | date_range
    status: str      # ok | warning | blocked
    message: str


@dataclass
class DQReport:
    checks: list[Check] = field(default_factory=list)
    source_rows: int | None = None
    schema: list | None = None       # [[kolom, tipe], ...] hasil run ini -- calon skema acuan berikutnya

    def add(self, name: str, status: str, message: str) -> None:
        self.checks.append(Check(name, status, message))

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=_RANK.get, default=UNCHECKED)

    def summary(self, statuses: tuple[str, ...] = (WARNING, BLOCKED)) -> str:
        return "; ".join(f"{c.name}: {c.message}" for c in self.checks if c.status in statuses)

    def to_json(self) -> str:
        return json.dumps([c.__dict__ for c in self.checks], ensure_ascii=False)


class DQBlocked(Exception):
    """Pemeriksaan berat gagal -- upload ke S3 ditahan."""

    def __init__(self, report: DQReport):
        super().__init__("Ditahan DQ: " + report.summary((BLOCKED,)))
        self.report = report


@dataclass
class AuditContext:
    target_date: str                 # tanggal proses (YYYY-MM-DD)
    schema_baseline: list | None     # jobs.schema_json; None = belum ada (run ini menjadi acuan)


# ---------------------------------------------------------------------------
# Dipanggil export.run_job()
# ---------------------------------------------------------------------------

def count_source(conn, job) -> tuple[int | None, str | None]:
    """
    COUNT_BIG(*) sumber dengan filter yang sama, di koneksi yang sama, TEPAT SEBELUM ekstraksi.
    Return (jumlah, None), atau (None, pesan) kalau gagal -- gagal menghitung TIDAK menahan upload.
    """
    if not job.count_query:
        return None, None
    try:
        cur = conn.cursor()
        try:
            if job.params:
                cur.execute(job.count_query, job.params)
            else:
                cur.execute(job.count_query)
            return int(cur.fetchone()[0]), None
        finally:
            cur.close()   # pyodbc: statement harus selesai sebelum query ekspor di koneksi yang sama
    except Exception as e:  # noqa: BLE001 -- error apa pun di sini cukup jadi peringatan
        return None, str(e).splitlines()[0][:300]


def schema_signature(schema: pa.Schema) -> list:
    return [[f.name, str(f.type)] for f in schema if f.name not in AUDIT_COLUMNS]


def date_column_index(field_names: list[str], date_check: dict | None) -> int | None:
    """Posisi kolom tanggal di hasil (nama dicocokkan tanpa beda huruf besar/kecil, seperti SQL Server)."""
    if not date_check:
        return None
    wanted = date_check["column"].lower()
    return next((i for i, n in enumerate(field_names) if n.lower() == wanted), None)


class MinMax:
    """Min/max satu kolom, diakumulasi per batch selama streaming (tanpa membaca ulang file)."""

    def __init__(self):
        self.min = self.max = None

    def update(self, column) -> None:
        mm = pc.min_max(column)
        lo, hi = mm["min"].as_py(), mm["max"].as_py()
        if lo is not None and (self.min is None or lo < self.min):
            self.min = lo
        if hi is not None and (self.max is None or hi > self.max):
            self.max = hi


def audit(job, ctx: AuditContext, source_rows: int | None, source_error: str | None, written_rows: int,
          schema: pa.Schema, date_found: bool, date_minmax: MinMax | None) -> DQReport:
    report = DQReport(source_rows=source_rows, schema=schema_signature(schema))
    _check_rows(report, job, ctx, source_rows, source_error, written_rows)
    _check_schema(report, ctx.schema_baseline)
    _check_date_range(report, job.date_check, written_rows, date_found, date_minmax)
    return report


def should_save_baseline(job, result, ctx: AuditContext) -> bool:
    """
    Skema acuan diperbarui hanya setelah data DIPUBLIKASIKAN ke tujuannya: job ber-S3 -> setelah
    upload sukses (run --skip-s3 tidak menggeser acuan); job tanpa S3 -> setelah tulis lokal sukses.
    """
    report = getattr(result, "dq", None)
    if report is None or report.schema is None or report.status == BLOCKED:
        return False
    published = result.s3_key is not None or job.s3_key is None
    return published and report.schema != ctx.schema_baseline


# ---------------------------------------------------------------------------
# Pemeriksaan
# ---------------------------------------------------------------------------

def _is_closed_period(job, target_date: str) -> bool:
    """Job harian untuk tanggal yang sudah lewat (<= kemarin WIB) -> data sumber dianggap final."""
    return job.needs_date and target_date <= default_target_date()


def _check_rows(report, job, ctx, source_rows, source_error, written_rows) -> None:
    if not job.count_query:
        return
    if source_error:
        report.add("rows", WARNING, f"gagal menghitung baris sumber ({source_error})")
    elif source_rows == written_rows:
        report.add("rows", OK, f"sumber = tertulis ({written_rows:,})")
    elif _is_closed_period(job, ctx.target_date):
        report.add("rows", BLOCKED, f"sumber {source_rows:,} != tertulis {written_rows:,}")
    else:
        report.add("rows", WARNING, f"sumber {source_rows:,} != tertulis {written_rows:,} "
                                    f"(data sumber masih berubah)")


def _check_schema(report, baseline) -> None:
    current = report.schema
    if baseline is None:
        report.add("schema", OK, f"skema acuan baru ({len(current)} kolom)")
        return
    old = {n.lower(): (n, t) for n, t in baseline}
    new = {n.lower(): (n, t) for n, t in current}
    removed = [old[k][0] for k in old if k not in new]
    added = [new[k][0] for k in new if k not in old]
    changed = [f"{new[k][0]} ({old[k][1]} -> {new[k][1]})" for k in new if k in old and new[k][1] != old[k][1]]
    if removed or changed:
        parts = ([f"kolom hilang: {', '.join(removed)}"] if removed else []) + \
                ([f"tipe berubah: {', '.join(changed)}"] if changed else [])
        report.add("schema", BLOCKED, "; ".join(parts))
    elif added:
        report.add("schema", WARNING, f"kolom baru: {', '.join(added)}")
    else:
        report.add("schema", OK, f"sama dengan acuan ({len(current)} kolom)")


def _check_date_range(report, spec, written_rows, date_found, mm) -> None:
    if not spec or written_rows == 0:
        return
    col = spec["column"]
    if not date_found:
        report.add("date_range", WARNING, f"kolom tanggal {col} tidak ada di hasil")
        return
    if mm.min is None:
        report.add("date_range", WARNING, f"kolom tanggal {col} kosong semua")
        return
    if "equals" in spec:
        want = str(spec["equals"])
        # rstrip: kesetaraan VARCHAR di SQL Server mengabaikan spasi di belakang
        ok = str(mm.min).rstrip() == want and str(mm.max).rstrip() == want
        expected = f"= {want}"
    else:
        lo, hi = spec["min"], spec["max_excl"]
        try:
            ok = _comparable(mm.min, lo) >= lo and _comparable(mm.max, hi) < hi
        except TypeError:
            report.add("date_range", WARNING, f"{col} bertipe lain dari yang diharapkan, tidak bisa dicek")
            return
        expected = f"{spec['min']} s/d sebelum {spec['max_excl']}"
    if ok:
        report.add("date_range", OK, f"{col} dalam rentang ({mm.min} s/d {mm.max})")
    else:
        report.add("date_range", BLOCKED, f"{col} di luar tanggal proses: {mm.min} s/d {mm.max} (harus {expected})")


def _comparable(value, like):
    """date vs datetime tidak bisa dibandingkan langsung di Python -- samakan ke datetime."""
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime) and isinstance(like, _dt.datetime):
        return _dt.datetime.combine(value, _dt.time())
    return value
