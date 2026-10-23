"""
Akses metadata SQL Server (untuk wizard & validasi job) dan TEST RUN draf job.

Semua query metadata hanya membaca INFORMATION_SCHEMA / sys.* -- identifier yang disisipkan
(nama database) sudah dicocokkan dulu dengan daftar sys.databases, dan nama tabel/kolom
dicocokkan dengan INFORMATION_SCHEMA sebelum dipakai.
"""

from __future__ import annotations

import base64
import datetime as _dt
import decimal
import re
import threading
import time
from datetime import datetime, timedelta

import pyodbc

from config import CONN_STR
from exporter import _advance_to_resultset_with_columns, build_schema, rows_to_table
from job import Job
from job_templates import STRDATE_FORMATS

_CACHE_TTL_S = 120
_LOGIN_TIMEOUT_S = 15
TEST_TIMEOUT_S = 90
TEST_MAX_ROWS = 500
_test_slots = threading.BoundedSemaphore(2)  # maks 2 test run bersamaan -> beban SQL Server terkendali


class CatalogError(Exception):
    pass


class TestRunBusy(Exception):
    pass


def _connect() -> pyodbc.Connection:
    try:
        return pyodbc.connect(CONN_STR, timeout=_LOGIN_TIMEOUT_S)
    except pyodbc.Error as e:
        raise CatalogError(f"Tidak bisa terhubung ke SQL Server: {e}") from e


def _q(name: str) -> str:
    """[nama] dengan ] di-escape -- dipakai SETELAH nama dicocokkan dengan katalog."""
    return "[" + name.replace("]", "]]") + "]"


class SqlServerCatalog:
    """Implementasi job_validation.Catalog + helper wizard. Thread-safe, cache TTL singkat."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, object]] = {}

    def _cached(self, key: tuple, loader):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < _CACHE_TTL_S:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def _query(self, sql: str, params: tuple = ()) -> list:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params) if params else cur.execute(sql)
            return cur.fetchall()
        except pyodbc.Error as e:
            raise CatalogError(f"Query metadata gagal: {e}") from e
        finally:
            conn.close()

    # --- job_validation.Catalog -------------------------------------------------
    def databases(self) -> list[str]:
        return self._cached(("db",), lambda: [r[0] for r in self._query(
            "SET NOCOUNT ON; SELECT name FROM sys.databases WHERE state = 0 AND HAS_DBACCESS(name) = 1 "
            "AND name NOT IN ('master', 'tempdb', 'model', 'msdb') ORDER BY name")])

    def _real_db(self, db: str) -> str:
        real = next((d for d in self.databases() if d.lower() == (db or "").lower()), None)
        if not real:
            raise CatalogError(f"Database '{db}' tidak ditemukan / tidak bisa diakses.")
        return real

    def tables(self, db: str) -> dict[str, dict]:
        # Jumlah baris SENGAJA tidak diambil di sini: join sys.partitions untuk semua tabel butuh
        # 40-60+ detik di database dengan ribuan tabel (terukur) -- lihat table_rows().
        db = self._real_db(db)

        def load():
            rows = self._query(
                f"SET NOCOUNT ON; SELECT TABLE_NAME, TABLE_TYPE FROM {_q(db)}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA = 'dbo' ORDER BY TABLE_NAME")
            return {r[0]: {"type": "view" if r[1] == "VIEW" else "table"} for r in rows}
        return self._cached(("tables", db.lower()), load)

    def table_rows(self, db: str, table: str) -> int | None:
        """Perkiraan jumlah baris SATU tabel (metadata sys.partitions, ~0,1 detik). None untuk view."""
        db = self._real_db(db)
        real_table = next((t for t in self.tables(db) if t.lower() == (table or "").lower()), None)
        if not real_table or self.tables(db)[real_table]["type"] == "view":
            return None

        def load():
            r = self._query(f"SET NOCOUNT ON; SELECT SUM(p.rows) FROM {_q(db)}.sys.partitions p "
                            f"WHERE p.object_id = OBJECT_ID(?) AND p.index_id IN (0, 1)",
                            (f"{_q(db)}.[dbo].{_q(real_table)}",))
            return int(r[0][0]) if r and r[0][0] is not None else None
        return self._cached(("rows", db.lower(), real_table.lower()), load)

    def columns(self, db: str, table: str) -> dict[str, dict]:
        db = self._real_db(db)
        real_table = next((t for t in self.tables(db) if t.lower() == (table or "").lower()), None)
        if not real_table:
            raise CatalogError(f"Tabel dbo.{table} tidak ada di {db}.")

        def load():
            rows = self._query(
                f"""SET NOCOUNT ON;
                SELECT c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH, c.IS_NULLABLE,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM {_q(db)}.sys.index_columns ic
                           JOIN {_q(db)}.sys.columns sc ON sc.object_id = ic.object_id AND sc.column_id = ic.column_id
                           WHERE ic.object_id = OBJECT_ID(?) AND ic.key_ordinal = 1 AND sc.name = c.COLUMN_NAME
                       ) THEN 1 ELSE 0 END
                FROM {_q(db)}.INFORMATION_SCHEMA.COLUMNS c
                WHERE c.TABLE_SCHEMA = 'dbo' AND c.TABLE_NAME = ?
                ORDER BY c.ORDINAL_POSITION""",
                (f"{_q(db)}.[dbo].{_q(real_table)}", real_table))
            return {r[0]: {"type": r[1].lower(), "max_length": r[2], "nullable": r[3] == "YES",
                           "leading_index": bool(r[4])} for r in rows}
        return self._cached(("cols", db.lower(), real_table.lower()), load)

    # --- helper wizard ------------------------------------------------------------
    def sample_values(self, db: str, table: str, column: str, n: int = 12) -> list:
        """
        Beberapa nilai NON-NULL kolom. Kalau kolom punya index (kolom pertama), ambil yang TERBARU
        (ORDER BY ... DESC = index seek, murah) -- paling mewakili data sekarang. Tanpa index: TANPA
        ORDER BY (sort di tabel besar mahal), jadi contohnya baris "pertama" yang bisa saja data lama.
        """
        db = self._real_db(db)
        cols = self.columns(db, table)
        real_col = next((c for c in cols if c.lower() == column.lower()), None)
        if not real_col:
            raise CatalogError(f"Kolom '{column}' tidak ada.")
        real_table = next(t for t in self.tables(db) if t.lower() == table.lower())
        order = f" ORDER BY {_q(real_col)} DESC" if cols[real_col]["leading_index"] else ""
        # DISTINCT + TOP: SQL Server memakai "flow distinct" (berhenti setelah n nilai unik), tidak
        # meng-hash seluruh tabel.
        rows = self._query(f"SET NOCOUNT ON; SELECT DISTINCT TOP {int(n)} {_q(real_col)} FROM {_q(db)}.[dbo].{_q(real_table)} "
                           f"WITH (NOLOCK) WHERE {_q(real_col)} IS NOT NULL{order}")
        return [r[0] for r in rows]


CATALOG = SqlServerCatalog()


def detect_date_format(values: list, data_type: str) -> dict:
    """
    Tebak cara memfilter kolom tanggal dari contoh nilainya -> {"template", "date_format", "message", "ok"}.
    Ini yang mencegah ulang kasus PCS_*_GIS: format 'DD.MM.YYYY' + filter rentang = selalu 0 baris.
    """
    # Cukup MAYORITAS contoh (>= 75%) yang cocok -- data lama/sampah sebagian kecil baris tidak boleh
    # menggagalkan deteksi (terukur: 252 dari 9,1 juta baris satu tabel epoch-ms berisi nilai kecil).
    def verdict(matches: int, total: int, ok_msg: str, **kw) -> dict | None:
        if not total or matches / total < 0.75:
            return None
        odd = total - matches
        note = f" ({odd} dari {total} contoh di luar format — data lama/tidak valid, tetap ikut terfilter apa adanya)" if odd else ""
        return {"ok": True, "message": ok_msg + note, **kw}

    if data_type in ("bigint",):
        ints = [v for v in values if isinstance(v, int)]
        ms = [v for v in ints if 10**12 <= v <= 4.1 * 10**12]
        shown = [(datetime(1970, 1, 1) + timedelta(milliseconds=v)).strftime("%Y-%m-%d %H:%M") for v in ms[:3]]
        found = verdict(len(ms), len(ints), f"Epoch milidetik — contoh: {', '.join(shown)}",
                        template="bronze_daily_epoch_ms", date_format=None)
        if found:
            return found
        if ints and sum(10**9 <= v <= 4.1 * 10**9 for v in ints) / len(ints) >= 0.75:
            return {"ok": False, "template": None, "date_format": None,
                    "message": "Nilainya terlihat seperti epoch DETIK (10 digit) — belum didukung template mana pun."}
        return {"ok": False, "template": None, "date_format": None,
                "message": "Nilai bukan epoch milidetik (13 digit) — kolom ini tidak bisa dipakai sebagai tanggal."}

    strs = [str(v).strip() for v in values if v is not None]
    if not strs:
        return {"ok": False, "template": None, "date_format": None, "message": "Kolom kosong — format tidak bisa dicek."}
    iso = sum(bool(re.match(r"^\d{4}-\d{2}-\d{2}", s)) and _parses(s[:10], "%Y-%m-%d") for s in strs)
    found = verdict(iso, len(strs), f"Format YYYY-MM-DD — filter rentang per hari. Contoh: {', '.join(strs[:3])}",
                    template="bronze_daily_strdate", date_format=None)
    if found:
        return found
    for fmt, label in STRDATE_FORMATS.items():
        found = verdict(sum(_parses(s, fmt) for s in strs), len(strs),
                        f"Format {label} — difilter dengan KESETARAAN tanggal. Contoh: {', '.join(strs[:3])}",
                        template="bronze_daily_strdate_eq", date_format=fmt)
        if found:
            return found
    return {"ok": False, "template": None, "date_format": None,
            "message": f"Format tanggal tidak dikenali (contoh: {', '.join(strs[:3])}) — tidak bisa difilter per hari."}


def _parses(s: str, fmt: str) -> bool:
    try:
        return datetime.strptime(s, fmt).strftime(fmt) == s  # harus bolak-balik persis (tanpa jam/sisa teks)
    except ValueError:
        return False


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return f"<{len(b)} bytes: {base64.b16encode(b[:12]).decode()}{'…' if len(b) > 12 else ''}>"
    return str(v)


ADHOC_TIMEOUT_S = 600
_export_slots = threading.BoundedSemaphore(2)  # maks 2 ekspor ad-hoc bersamaan


def export_parquet(job: Job, path: str, max_rows: int, compression: str = "zstd") -> dict:
    """
    Tulis hasil query job ke file .parquet (ekspor ad-hoc, TANPA S3). Skema sama dengan file datalake
    (termasuk kolom _ingested_at). Maks `max_rows` baris -- SET ROWCOUNT max_rows+1 supaya bisa tahu
    datanya terpotong tanpa membaca seluruh tabel. Dalam transaksi yang di-ROLLBACK, sama seperti test_run.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import timezone

    if not _export_slots.acquire(blocking=False):
        raise TestRunBusy("Sedang ada 2 ekspor lain berjalan — coba lagi sebentar.")
    try:
        conn = _connect()
        conn.timeout = ADHOC_TIMEOUT_S
        started = time.monotonic()
        writer = None
        total, truncated = 0, False
        try:
            cur = conn.cursor()
            cur.execute(f"SET ROWCOUNT {int(max_rows) + 1}")
            cur.execute(job.query, job.params) if job.params else cur.execute(job.query)
            if not _advance_to_resultset_with_columns(cur):
                raise CatalogError("Query tidak menghasilkan resultset berkolom.")
            schema = build_schema(cur, job.overrides)
            schema = schema.append(pa.field("_ingested_at", pa.timestamp("us", tz="UTC")))
            ingested_at = datetime.now(timezone.utc)
            writer = pq.ParquetWriter(path, schema, compression=compression)
            while True:
                rows = cur.fetchmany(20_000)
                if not rows:
                    break
                if total + len(rows) > max_rows:
                    rows = rows[: max_rows - total]
                    truncated = True
                if rows:
                    writer.write_table(rows_to_table(rows, schema, ingested_at))
                    total += len(rows)
                if truncated:
                    break
            if total == 0:
                writer.write_table(rows_to_table([], schema, ingested_at))
        finally:
            if writer is not None:
                writer.close()
            try:
                conn.rollback()
            finally:
                conn.close()
        return {"rows": total, "truncated": truncated, "elapsed_s": round(time.monotonic() - started, 2)}
    finally:
        _export_slots.release()


def test_run(job: Job, limit: int = 100) -> dict:
    """
    Jalankan query job dengan batas `limit` baris TANPA menulis file / upload. Aman:
      - SET ROWCOUNT -> SQL Server berhenti setelah `limit` baris (bukan memindai lalu dibuang);
      - semua dijalankan di dalam transaksi yang SELALU di-ROLLBACK (pyodbc autocommit=False),
        jadi kalau query/SP sempat menulis, tulisan itu dibatalkan;
      - query timeout TEST_TIMEOUT_S detik.
    """
    limit = max(1, min(int(limit), TEST_MAX_ROWS))
    if not _test_slots.acquire(blocking=False):
        raise TestRunBusy("Sedang ada 2 uji coba lain berjalan — coba lagi sebentar.")
    try:
        conn = _connect()
        conn.timeout = TEST_TIMEOUT_S
        started = time.monotonic()
        try:
            cur = conn.cursor()
            cur.execute(f"SET ROWCOUNT {limit}")
            cur.execute(job.query, job.params) if job.params else cur.execute(job.query)
            if not _advance_to_resultset_with_columns(cur):
                return {"rows": 0, "limit": limit, "limit_hit": False, "elapsed_s": time.monotonic() - started,
                        "columns": [], "sample": [], "message": "Query tidak menghasilkan resultset berkolom."}
            sql_types = [getattr(d[1], "__name__", str(d[1])) for d in cur.description]
            schema = build_schema(cur, job.overrides)
            rows = cur.fetchmany(limit)
            elapsed = time.monotonic() - started
            rows_to_table(rows, schema)  # pastikan konversi ke Arrow berhasil (mis. decimal, tipe override)
            return {
                "rows": len(rows),
                "limit": limit,
                "limit_hit": len(rows) >= limit,
                "elapsed_s": round(elapsed, 2),
                "columns": [{"name": f.name, "arrow_type": str(f.type), "python_type": t,
                             "overridden": f.name in job.overrides} for f, t in zip(schema, sql_types)],
                "sample": [[_jsonable(v) for v in r] for r in rows[:20]],
                "message": None,
            }
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
    finally:
        _test_slots.release()
