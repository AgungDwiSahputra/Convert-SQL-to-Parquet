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
  volume      : jumlah baris vs median riwayat sesuai IRAMA job (jobs.cadence) -- hanya peringatan.
                Harian & snapshot: per dataset, vs 14 tanggal terakhir. Mingguan & bulanan: per PERIODE
                (total 1 minggu / 1 bulan yang sudah lengkap) vs 8 minggu / 6 bulan sebelumnya, dinilai
                setelah run (period_volume_findings) -- per hari volumenya memang tidak rata.
                Job periodik bronze (Job.period): satu hasil berlabel awal periode = seluruh periode.

Satu saja 'blocked' -> DQBlocked: upload ditahan, partisi lama di S3 tetap utuh, item dicatat gagal.
Perubahan skema yang disengaja: `python manage.py accept-schema <job>` lalu jalankan ulang.
Riwayat hasil (baseline volume) di tabel dq_results, diisi CLI & worker lewat after_job().
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import statistics
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.compute as pc

from pipeline import snapshots
from pipeline.app_settings import get_settings
from pipeline.job_store import (
    default_target_date, dq_latest_rows, get_job_row, get_schema_baseline, record_dq_result, save_schema_baseline,
)
from pipeline.job_templates import default_cadence, period_kind
from pipeline.periods import period_bounds

OK, WARNING, BLOCKED, UNCHECKED = "ok", "warning", "blocked", "unchecked"
_RANK = {UNCHECKED: 0, OK: 1, WARNING: 2, BLOCKED: 3}
AUDIT_COLUMNS = {"_ingested_at"}     # kolom teknis pipeline, bukan dari sumber -- tidak ikut skema acuan
ITEM_HISTORY = {"daily": 14, "snapshot": 14}     # irama yang volumenya dinilai per dataset: jumlah riwayat
PERIOD_HISTORY = {"weekly": 8, "monthly": 6}     # irama yang volumenya dinilai per periode


@dataclass
class Check:
    name: str        # rows | schema | date_range | volume
    status: str      # ok | warning | blocked | unchecked (mis. riwayat volume belum cukup)
    message: str


@dataclass
class DQReport:
    checks: list[Check] = field(default_factory=list)
    source_rows: int | None = None
    written_rows: int | None = None
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
class VolumeRules:
    low_pct: float = 50
    high_pct: float = 200
    min_history: int = 7

    @classmethod
    def load(cls, conn) -> VolumeRules:
        s = get_settings(conn, "dq.")
        return cls(s["dq.volume_low_pct"], s["dq.volume_high_pct"], int(s["dq.volume_min_history"]))


@dataclass
class AuditContext:
    target_date: str                 # tanggal proses (YYYY-MM-DD)
    schema_baseline: list | None     # jobs.schema_json; None = belum ada (run ini menjadi acuan)
    cadence: str = "daily"
    volume_history: list | None = None   # baris tanggal-tanggal sebelumnya; None = volume tidak dinilai per dataset
    rules: VolumeRules = field(default_factory=VolumeRules)
    snapshot_prev: dict | None = None    # versi snapshot master terakhir (snapshots.py); None = belum ada


# ---------------------------------------------------------------------------
# Dipanggil CLI (exporter.py) & worker, di sekitar run_job()
# ---------------------------------------------------------------------------

def cadence_of(conn, name: str, template: str, args: dict) -> str:
    """Irama job saat ini (jobs.cadence, atau tebakan template kalau kosong / job sudah tidak ada)."""
    row = get_job_row(conn, name)
    return row.effective_cadence if row else default_cadence(template, args)


def prepare(conn, name: str, target_date: str, cadence: str) -> AuditContext:
    """Konteks pemeriksaan satu dataset: skema acuan, irama, riwayat volume, ambang (tabel settings)."""
    history = None
    if cadence in ITEM_HISTORY:
        history = [r["rows"] for r in dq_latest_rows(conn, name)
                   if r["target_date"] != target_date and r["rows"] is not None][:ITEM_HISTORY[cadence]]
    return AuditContext(target_date, get_schema_baseline(name, conn), cadence, history, VolumeRules.load(conn),
                        snapshots.latest(conn, name))


def after_job(conn, job, ctx: AuditContext, result=None, error: Exception | None = None,
              run_id: int | None = None) -> None:
    """
    Setelah run_job(): catat hasil ke dq_results (baseline volume) dan perbarui skema acuan kalau data
    sudah dipublikasikan. result = JobResult (sukses) atau error = DQBlocked (ditahan); error lain diabaikan.
    """
    if result is not None:
        report = result.dq
        published = result.s3_key is not None or job.s3_key is None
        if should_save_baseline(job, result, ctx):
            save_schema_baseline(job.name, report.schema, conn)
        snapshots.record(conn, job.name, ctx.target_date, ctx.snapshot_prev, result, run_id)
    else:
        report, published = getattr(error, "report", None), False
    if report is not None:
        record_dq_result(conn, job.name, ctx.target_date, report.written_rows, report.source_rows, report.status,
                         published, report.to_json(), run_id)


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


def date_specs(date_check) -> list[dict]:
    """job.date_check -> daftar spec (kosong / satu dict / daftar dict)."""
    if not date_check:
        return []
    return [date_check] if isinstance(date_check, dict) else list(date_check)


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
          schema: pa.Schema, date_checks: list[tuple[dict, bool, MinMax | None]]) -> DQReport:
    """date_checks: satu entri per spec job.date_check -- (spec, kolomnya ada di hasil, min/max-nya)."""
    report = DQReport(source_rows=source_rows, written_rows=written_rows, schema=schema_signature(schema))
    _check_rows(report, job, ctx, source_rows, source_error, written_rows)
    _check_schema(report, ctx.schema_baseline)
    for spec, found, mm in date_checks:
        _check_date_range(report, spec, written_rows, found, mm)
    _check_volume(report, ctx, written_rows)
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
    """
    Job harian untuk tanggal yang sudah lewat (<= kemarin WIB) -> data sumber dianggap final. Job periodik
    (mingguan/bulanan): final kalau AKHIR periodenya sudah lewat -- periode yang masih berjalan terus bertambah.
    """
    end = job.period[1] if job.period else target_date
    return job.needs_date and end <= default_target_date()


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
        ok = _same(mm.min, want) and _same(mm.max, want)
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


def _check_volume(report, ctx, written_rows) -> None:
    hist = ctx.volume_history
    if hist is None:
        return
    if len(hist) < ctx.rules.min_history:
        report.add("volume", UNCHECKED, f"riwayat belum cukup ({len(hist)} dari {ctx.rules.min_history} tanggal)")
        return
    status, message = _volume_verdict(written_rows, hist, ctx.rules, "tanggal")
    report.add("volume", status, message)


def _volume_verdict(value: int, history: list, rules: VolumeRules, unit: str) -> tuple[str, str]:
    med = statistics.median(history)
    if med == 0:
        return OK, f"{value:,} baris (median riwayat 0)"
    pct = value * 100 / med
    text = f"{value:,} baris = {pct:.0f}% dari median {med:,.0f} ({len(history)} {unit} terakhir)"
    return (WARNING if pct < rules.low_pct or pct > rules.high_pct else OK), text


def period_of(cadence: str, d: _dt.date) -> tuple[_dt.date, _dt.date]:
    """Periode yang memuat tanggal d: minggu Senin-Minggu, atau satu bulan kalender."""
    return period_bounds("week" if cadence == "weekly" else "month", d)


def run_period_findings(conn, executed: dict) -> list[dict]:
    """executed = {job_name: (irama, {tanggal sukses})} dari satu run -> temuan volume per periode."""
    rules = VolumeRules.load(conn)
    findings = []
    for name, (cadence, dates) in executed.items():
        row = get_job_row(conn, name)
        findings += period_volume_findings(conn, name, cadence, dates, rules,
                                           whole_period=bool(row and period_kind(row.template)))
    return findings


def period_volume_findings(conn, name: str, cadence: str, target_dates, rules: VolumeRules,
                           whole_period: bool = False) -> list[dict]:
    """
    Volume per periode untuk job mingguan/bulanan: total periode yang memuat target_dates (hanya kalau
    periodenya LENGKAP) vs median total periode lengkap sebelumnya. Lengkap = semua tanggal periode sudah
    punya hasil (job yang dijalankan per tanggal), atau -- whole_period, job periodik bronze yang sekali run
    menarik seluruh periode dan dilabeli awal periode -- periodenya sudah lewat (akhir <= kemarin WIB).
    Return hanya temuan peringatan: [{job_name, period, message}].
    """
    if cadence not in PERIOD_HISTORY:
        return []
    totals, counts = {}, {}
    for r in dq_latest_rows(conn, name):
        p = period_of(cadence, _dt.date.fromisoformat(r["target_date"]))
        totals[p] = totals.get(p, 0) + (r["rows"] or 0)
        counts[p] = counts.get(p, 0) + 1
    yesterday = _dt.date.fromisoformat(default_target_date())
    complete = {p: t for p, t in totals.items()
                if (p[1] <= yesterday if whole_period else counts[p] == (p[1] - p[0]).days + 1)}
    unit = "minggu" if cadence == "weekly" else "bulan"
    need = max(3, PERIOD_HISTORY[cadence] // 2)
    findings = []
    for p in sorted({period_of(cadence, _dt.date.fromisoformat(t)) for t in target_dates}):
        if p not in complete:
            continue   # periode belum lengkap -> dinilai saat sudah lengkap
        history = [complete[q] for q in sorted(complete, reverse=True) if q[0] < p[0]][:PERIOD_HISTORY[cadence]]
        if len(history) < need:
            continue
        status, message = _volume_verdict(complete[p], history, rules, unit)
        if status == WARNING:
            findings.append({"job_name": name, "period": f"{p[0]} s/d {p[1]}", "message": message})
    return findings


def _same(value, want: str) -> bool:
    """
    Nilai hasil sama dengan nilai filter. rstrip: kesetaraan VARCHAR di SQL Server mengabaikan spasi di belakang.
    Kolom desimal (mis. decimal(10,2)) dibaca '10.00' padahal filternya 10 -> dibandingkan sebagai angka.
    """
    s = str(value).rstrip()
    if s == want:
        return True
    try:
        return decimal.Decimal(s) == decimal.Decimal(want)
    except decimal.InvalidOperation:
        return False


def _comparable(value, like):
    """date vs datetime tidak bisa dibandingkan langsung di Python -- samakan ke datetime."""
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime) and isinstance(like, _dt.datetime):
        return _dt.datetime.combine(value, _dt.time())
    return value
