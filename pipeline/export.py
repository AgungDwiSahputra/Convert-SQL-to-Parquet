"""
MESIN EKSPOR SQL Server 2012 -> Parquet (dataset AI/ML) -- dipakai CLI (exporter.py di folder
proyek), worker.py, dan Web UI (test run / ekspor cepat). Jarang perlu disentuh.

SQL Server 2012 tidak punya dukungan Parquet native. Pola:
    tarik data via pyodbc (streaming, fetchmany) -> tulis Parquet per-batch dengan ParquetWriter.

Schema = CAMPUR (auto default, manual kalau perlu):
  - Tipe kolom dideteksi OTOMATIS dari metadata SQL Server (cursor.description).
  - decimal/numeric -> decimal128(p,s) (presisi terjaga, BUKAN float).
  - datetime/datetime2 -> timestamp[us]; bit -> boolean; uniqueidentifier -> string.
  - Override manual per kolom lewat field 'overrides' di tiap Job (disimpan per job di jobs.db).
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
import pyodbc
import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.config import (
    AWS_ACCESS_KEY_ID, AWS_REGION, AWS_SECRET_ACCESS_KEY, LOG_DIR, OUTPUT_DIR, S3_BUCKET_NAME,
)


# ---------------------------------------------------------------------------
# AUTO-DETECT SCHEMA - dari metadata pyodbc -> tipe Arrow
# ---------------------------------------------------------------------------

def arrow_type_from_description(col_desc) -> pa.DataType:
    """
    Petakan satu entri cursor.description -> tipe Arrow.
    cursor.description item: (name, type_code, display_size, internal_size, precision, scale, null_ok)
    """
    import datetime as _dt
    import decimal as _dec

    _name, type_code, _disp, _int, precision, scale, _null = col_desc

    if type_code is _dec.Decimal:
        p = precision if precision and precision > 0 else 38
        s = scale if scale is not None and scale >= 0 else 0
        if s > p:
            p = s
        return pa.decimal128(p, s)
    if type_code is bool:
        return pa.bool_()
    if type_code is int:
        return pa.int64()
    if type_code is float:
        return pa.float64()
    if type_code is _dt.datetime:
        return pa.timestamp("us")     # asumsi WIB; SQL Server tidak simpan timezone
    if type_code is _dt.date:
        return pa.date32()
    if type_code is _dt.time:
        return pa.time64("us")
    if type_code in (bytes, bytearray, memoryview):
        return pa.binary()
    return pa.string()   # str, uniqueidentifier, dll.


def build_schema(cursor, overrides: dict) -> pa.Schema:
    """Bangun schema Arrow dari cursor.description, terapkan override manual per nama kolom."""
    fields = []
    for col in cursor.description:
        col_name = col[0]
        dtype = overrides.get(col_name) or arrow_type_from_description(col)
        fields.append(pa.field(col_name, dtype))
    return pa.schema(fields)


# ---------------------------------------------------------------------------
# LOGIKA EKSPOR
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"

log = logging.getLogger("parquet-export")


def setup_logging(prefix: str = "exporter") -> str:
    """
    Log ke console DAN ke file logs/<prefix>_YYYYmmdd_HHMMSS.log sekaligus (dua handler) --
    supaya wrapper .bat/scheduler tidak perlu redirect/tee manual (redirect 2>&1 native exe
    di dalam PowerShell 5.1 malah membungkus tiap baris stderr jadi NativeCommandError, walau
    exit code 0). Nama file pakai strftime Python (BUKAN %date%/%time% batch) supaya konsisten
    lintas locale Windows.
    Dipanggil dari main() / worker.py, BUKAN saat import -- supaya web app bisa meng-import
    modul ini tanpa membuat file log baru.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, datetime.now().strftime(f"{prefix}_%Y%m%d_%H%M%S.log"))
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    log.info("Log run ini disimpan ke: %s", log_file)
    return log_file


@dataclass
class JobResult:
    """Hasil satu run_job() -- dicatat worker ke riwayat run (run_items)."""
    rows: int
    elapsed_s: float
    size_bytes: int = 0
    location: str | None = None   # path file lokal; None = hanya di memori / tidak ada resultset
    s3_key: str | None = None     # terisi HANYA kalau benar-benar diupload


def make_s3_client(skip_s3: bool):
    """Klien boto3 S3, atau None kalau skip_s3 / kredensial .env belum lengkap (upload dilewati)."""
    if skip_s3:
        return None
    if not (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and S3_BUCKET_NAME):
        log.warning("Kredensial S3 belum lengkap di .env, upload S3 dilewati untuk semua job.")
        return None
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )


def rows_to_table(rows: list, schema: pa.Schema, ingested_at: datetime | None = None) -> pa.Table:
    """
    Baris pyodbc -> pyarrow.Table sesuai schema. NULL (None) dibiarkan null.
    Kalau ingested_at diisi, field TERAKHIR di `schema` diasumsikan kolom audit
    '_ingested_at' (lihat run_job()) -- nilainya di-broadcast SAMA untuk semua baris,
    satu timestamp per batch tulis (bukan per baris), karena ini metadata pipeline
    (kapan batch ini ditulis), bukan data yang datang dari SQL Server.
    """
    data_fields = pa.schema(list(schema)[:-1]) if ingested_at is not None else schema
    columnar = list(zip(*rows)) if rows else [[] for _ in data_fields]
    arrays = [pa.array(list(vals), type=f.type) for f, vals in zip(data_fields, columnar)]
    if ingested_at is not None:
        arrays.append(pa.array([ingested_at] * len(rows), type=schema[-1].type))
    return pa.Table.from_arrays(arrays, schema=schema)


def resolve_output(output: str, date_suffix: str | None = None) -> str:
    """
    Gabungkan OUTPUT_DIR + nama file bila output bukan path absolut.
    date_suffix (mis. '20260701') disisipkan sebelum ekstensi -- dipakai waktu
    mode range supaya file lokal per-tanggal tidak saling menimpa.
    """
    if date_suffix:
        root, ext = os.path.splitext(output)
        output = f"{root}_{date_suffix}{ext}"
    if os.path.isabs(output):
        return output
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, output)


def advance_to_resultset_with_columns(cursor):
    """
    SP yang menjalankan EXEC sp_ETL_Load... lalu SELECT bisa menghasilkan beberapa
    resultset (atau resultset tanpa kolom dari statement non-SELECT). Lompati resultset
    yang cursor.description-nya None sampai ketemu yang punya kolom (SELECT sesungguhnya).
    Mengembalikan True bila ketemu resultset berkolom, False bila habis.
    """
    while cursor.description is None:
        if not cursor.nextset():
            return False
    return True


def run_job(conn: pyodbc.Connection, job, batch_size: int, compression, s3_client=None,
            date_suffix: str | None = None, skip_local: bool = False) -> JobResult:
    started = datetime.now()
    tag = job.name if date_suffix is None else f"{job.name}@{date_suffix}"

    write_to_s3 = job.s3_key is not None and s3_client is not None
    # skip_local cuma efektif kalau ada tujuan S3 -- kalau tidak, tetap simpan lokal
    # supaya hasil query (yang juga memicu ETL Load di production) tidak hilang percuma.
    use_local_file = not (skip_local and write_to_s3)

    if use_local_file:
        out_path = resolve_output(job.output, date_suffix)
        sink = out_path
    else:
        out_path = None
        sink = io.BytesIO()
        log.info("[%s] --skip-local: parquet ditulis di memori, langsung upload ke S3.", tag)

    if skip_local and use_local_file and not write_to_s3:
        log.warning("[%s] --skip-local diminta tapi job ini tidak ada tujuan S3 (s3_key kosong / S3 nonaktif) -- "
                    "tetap disimpan lokal.", tag)

    log.info("[%s] menjalankan query...", tag)

    cursor = conn.cursor()
    # Params dikirim terpisah (aman dari SQL injection). Urutan sesuai tanda '?' di query.
    if job.params:
        cursor.execute(job.query, job.params)
    else:
        cursor.execute(job.query)

    # SP dengan EXEC + SELECT: lompati resultset non-SELECT sampai ketemu yang berkolom.
    if not advance_to_resultset_with_columns(cursor):
        log.warning("[%s] query tidak menghasilkan resultset berkolom (tidak ada data). Dilewati.",
                    tag)
        return JobResult(rows=0, elapsed_s=(datetime.now() - started).total_seconds())

    schema = build_schema(cursor, job.overrides)
    data_field_names = [f.name for f in schema]  # kolom asli dari SQL Server, sebelum _ingested_at
    # Satu timestamp UTC per JOB RUN (bukan per-row/per-batch) -- konsisten dgn desain
    # overwrite-per-partition: re-run yang sama akan menimpa dengan _ingested_at baru.
    ingested_at = datetime.now(timezone.utc)
    schema = schema.append(pa.field("_ingested_at", pa.timestamp("us", tz="UTC")))
    log.info("[%s] schema: %s", tag, ", ".join(f"{f.name}:{f.type}" for f in schema))

    total = 0
    first_row = None
    writer: pq.ParquetWriter | None = None
    try:
        writer = pq.ParquetWriter(sink, schema, compression=compression)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            if first_row is None:
                first_row = dict(zip(data_field_names, rows[0]))
            writer.write_table(rows_to_table(rows, schema, ingested_at))
            total += len(rows)
            log.info("[%s]   +%s baris (total %s)", tag, f"{len(rows):,}", f"{total:,}")
    finally:
        if writer is not None:
            writer.close()

    elapsed = (datetime.now() - started).total_seconds()
    if use_local_file:
        size_bytes = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        location = out_path
    else:
        size_bytes = sink.getbuffer().nbytes
        location = None
    log.info("[%s] SELESAI: %s baris -> %s (%.1f MB) dalam %.1fs",
             tag, f"{total:,}", location or "(memori, tidak disimpan lokal)", size_bytes / (1024 * 1024),
             elapsed)
    if job.labels:
        log.info("[%s] kolom label (target model): %s", tag, job.labels)

    key = None
    if total > 0 and write_to_s3:
        key = job.s3_key(first_row)
        log.info("[%s] upload ke s3://%s/%s ...", tag, S3_BUCKET_NAME, key)
        if use_local_file:
            s3_client.upload_file(out_path, S3_BUCKET_NAME, key)
        else:
            sink.seek(0)
            s3_client.upload_fileobj(sink, S3_BUCKET_NAME, key)
        log.info("[%s] upload S3 selesai.", tag)
    elif total > 0 and job.s3_key and s3_client is None:
        log.warning("[%s] punya s3_key tapi upload dilewati (--skip-s3 atau kredensial S3 belum lengkap).",
                    tag)

    return JobResult(rows=total, elapsed_s=elapsed, size_bytes=size_bytes, location=location, s3_key=key)
