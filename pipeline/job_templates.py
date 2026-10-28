"""
TEMPLATE JOB -- mekanisme (bukan data) untuk membangun objek Job dari isian parameter.

Daftar job tidak lagi ditulis sebagai kode di jobs.py, tapi disimpan sebagai DATA di
database job (lihat job_store.py): satu baris = nama job + nama template + parameter
(JSON). File ini berisi template-template itu -- dipindahkan dari helper di dalam
build_jobs() jobs.py, dengan dua aturan:

  1. Job yang dihasilkan (query, params, output, S3 key) HARUS identik byte-per-byte
     dengan helper lama -- dibuktikan oleh `python migrate_jobs.py --check`. Karena itu
     whitespace di dalam string query sengaja disamakan persis dengan helper lama,
     walaupun indentasi kodenya di file ini berbeda.
  2. TIDAK ada nilai spesifik environment di sini (nama database, nama SP, IP linked
     server, dsb.) -- file ini masuk Git. Semua nilai itu disimpan eksplisit per job
     di database job (gitignored), sama seperti dulu tertulis di jobs.py (gitignored).

Semua template dipanggil dengan pola sama: template(name, target_date, **args) -> Job.
target_date berformat 'YYYY-MM-DD'.
"""

from __future__ import annotations

import re
from datetime import datetime as _datetime, timedelta as _timedelta
from typing import Callable

import pyarrow as pa

from pipeline.config import S3_PREFIX
from pipeline.job import Job

# Nama file parquet default di S3. KONSTAN antar-run (bukan auto-increment/timestamp),
# karena tiap job menulis data PENUH per partisi -- lihat README bagian 5 poin 3.
DEFAULT_PART_FILE = "part-00001.parquet"


# ---------------------------------------------------------------------------
# S3 KEY (Hive-style partitioning) -- konvensi lengkap di README bagian 5
# ---------------------------------------------------------------------------

def _key(*parts: str | None) -> str:
    """Rangkai S3 key: buang segmen kosong dan garis miring dobel."""
    return "/".join(p.strip("/") for p in parts if p)


def s3_daily(target_date: str, dataset: str, *, layer: str, module: str | None = None,
             file_name: str = DEFAULT_PART_FILE):
    """Transaksional harian: {layer}/{module}/{dataset}/recorded_year=/recorded_month=/recorded_day=/{file_name}"""
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    key = _key(S3_PREFIX, layer, module, dataset,
               f"recorded_year={dt:%Y}", f"recorded_month={dt:%m}", f"recorded_day={dt:%d}",
               file_name)
    return lambda _first_row: key


def s3_weekly(target_date: str, dataset: str, *, layer: str, module: str | None = None,
              file_name: str = DEFAULT_PART_FILE):
    """
    Transaksional mingguan: {layer}/{module}/{dataset}/recorded_year=/recorded_month=/recorded_week=W{n}/{file_name}
    Nilai minggu diambil dari kolom week_year/week_month/week_of_month baris PERTAMA hasil
    query (batas minggu ditentukan SQL Server, jangan dihitung ulang di Python).
    """
    def build(first_row):
        y = first_row["week_year"]
        m = first_row["week_month"]
        w = first_row["week_of_month"]
        return _key(S3_PREFIX, layer, module, dataset,
                    f"recorded_year={y}", f"recorded_month={m:02d}", f"recorded_week=W{w}",
                    file_name)
    return build


# Baris pertama hasil query palsu -- untuk menghitung contoh path S3 layout mingguan (s3_weekly
# membacanya) saat validasi job di Web UI dan parity check migrate_jobs.py.
SAMPLE_FIRST_ROW = {"week_year": 2026, "week_month": 7, "week_of_month": 3}


def s3_master(target_date: str, dataset: str, *, layer: str, module: str | None = None,
              file_name: str = DEFAULT_PART_FILE):
    """Master/dimensi, snapshot penuh per tanggal proses: {layer}/{module}/master_{dataset}/snapshot_date=/{file_name}"""
    key = _key(S3_PREFIX, layer, module, f"master_{dataset}",
               f"snapshot_date={target_date}", file_name)
    return lambda _first_row: key


S3_LAYOUTS = {"daily": s3_daily, "weekly": s3_weekly, "master": s3_master}


def s3_key_from_spec(spec: dict | None, target_date: str):
    """
    spec = {"layout": "daily"|"weekly"|"master", "dataset", "layer", "module", "file_name"}
    -> callable s3_key untuk Job. None/kosong = job tidak diupload ke S3.
    Dipakai template sp_job & custom_sql (template bronze_* menyusun S3 key-nya sendiri).
    """
    if not spec:
        return None
    spec = dict(spec)
    layout = spec.pop("layout")
    if layout not in S3_LAYOUTS:
        raise ValueError(f"layout S3 tidak dikenal: {layout!r} (pilihan: {', '.join(S3_LAYOUTS)})")
    return S3_LAYOUTS[layout](target_date, **spec)


# ---------------------------------------------------------------------------
# OVERRIDE TIPE KOLOM -- disimpan sebagai teks, BUKAN objek pyarrow
# ---------------------------------------------------------------------------

# Bentuk teks = str() dari tipe pyarrow-nya (mis. str(pa.float64()) == "double"), supaya
# yang tampil di log schema exporter sama dengan yang ditulis di override.
SIMPLE_ARROW_TYPES = {str(t()): t for t in (
    pa.string, pa.bool_, pa.int8, pa.int16, pa.int32, pa.int64,
    pa.float32, pa.float64, pa.date32, pa.binary,
)}


def parse_arrow_type(spec: str) -> pa.DataType:
    """
    'decimal128(18, 4)' -> pa.decimal128(18, 4). Whitelist + regex, BUKAN eval --
    nilai override nantinya bisa datang dari form web.
    """
    s = spec.strip()
    if s in SIMPLE_ARROW_TYPES:
        return SIMPLE_ARROW_TYPES[s]()
    m = re.fullmatch(r"decimal128\(\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        return pa.decimal128(int(m[1]), int(m[2]))
    m = re.fullmatch(r"timestamp\[(s|ms|us|ns)\]", s)
    if m:
        return pa.timestamp(m[1])
    raise ValueError(f"tipe override tidak dikenal: {spec!r} (contoh valid: 'decimal128(18, 4)', "
                     f"'timestamp[us]', {', '.join(repr(k) for k in SIMPLE_ARROW_TYPES)})")


def parse_overrides(overrides: dict[str, str] | None) -> dict[str, pa.DataType]:
    return {col: parse_arrow_type(t) for col, t in (overrides or {}).items()}


# ---------------------------------------------------------------------------
# TEMPLATE BRONZE -- raw copy tabel sumber, TANPA lewat dispatcher SP
# ---------------------------------------------------------------------------

def count_query_for(query: str) -> str:
    """
    Query hitung baris untuk pemeriksaan DQ (pipeline/dq.py): SELECT template dibungkus subquery,
    jadi filternya IDENTIK dengan query ekspor dan parameternya sama persis. Hanya untuk SELECT
    tanpa ORDER BY / CTE (semua template bronze kecuali ArcSDE yang memakai EXEC ... AT).
    """
    body = query.strip()
    if body.upper().startswith("SET NOCOUNT ON;"):
        body = body[len("SET NOCOUNT ON;"):].strip()
    return f"SET NOCOUNT ON; SELECT COUNT_BIG(*) FROM ({body}) AS dq_src"


# Semua template bronze menerima `overrides` (paksa tipe Arrow per kolom, teks -- lihat
# parse_arrow_type) & `labels` (kolom target ML) OPSIONAL. Default kosong = sama persis dengan
# helper lama di jobs.py (parity tetap identik).

def bronze_daily(name: str, target_date: str, *, table: str, date_col: str, dataset: str, db: str,
                 module: str | None = None, output: str | None = None,
                 overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    SELECT * mentah dari satu tabel, difilter satu hari kalender (00:00 s/d 24:00) lewat
    kolom tanggal ASLI bertipe datetime/date milik tabel itu sendiri.
    """
    output = output or f"{name}.parquet"
    query = f"""
            SET NOCOUNT ON;
            SELECT * FROM {db}.dbo.{table} WITH (NOLOCK, NOWAIT)
            WHERE {date_col} >= CAST(? AS DATE) AND {date_col} < DATEADD(DAY, 1, CAST(? AS DATE))
        """
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    return Job(name=name, query=query, params=[target_date, target_date], output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_daily(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=True, count_query=count_query_for(query),
               date_check={"column": date_col, "min": dt, "max_excl": dt + _timedelta(days=1)})


def bronze_daily_strdate(name: str, target_date: str, *, table: str, date_col: str, dataset: str, db: str,
                         module: str | None = None, output: str | None = None,
                         overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Sama seperti bronze_daily(), tapi date_col bertipe VARCHAR. Membandingkan string dengan
    rentang 'YYYY-MM-DD' -- HANYA benar kalau isi kolomnya memang berformat 'YYYY-MM-DD'.
    Format lain (mis. 'DD.MM.YYYY') akan selalu menghasilkan 0 baris tanpa error; cek
    catatan (notes) job terkait sebelum mengaktifkannya.
    """
    output = output or f"{name}.parquet"
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    next_day = (dt + _timedelta(days=1)).strftime("%Y-%m-%d")
    query = f"""
            SET NOCOUNT ON;
            SELECT * FROM {db}.dbo.{table} WITH (NOLOCK, NOWAIT)
            WHERE {date_col} >= ? AND {date_col} < ?
        """
    return Job(name=name, query=query, params=[target_date, next_day], output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_daily(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=True, count_query=count_query_for(query),
               date_check={"column": date_col, "min": target_date, "max_excl": next_day})


# Format tanggal string yang didukung bronze_daily_strdate_eq (strftime). Whitelist -- nilainya
# datang dari form web dan disisipkan sebagai PARAMETER query (bukan teks SQL).
STRDATE_FORMATS = {
    "%d.%m.%Y": "DD.MM.YYYY",
    "%d/%m/%Y": "DD/MM/YYYY",
    "%d-%m-%Y": "DD-MM-YYYY",
    "%Y%m%d": "YYYYMMDD",
    "%Y/%m/%d": "YYYY/MM/DD",
    "%Y-%m-%d": "YYYY-MM-DD",
}


def bronze_daily_strdate_eq(name: str, target_date: str, *, table: str, date_col: str, date_format: str,
                            dataset: str, db: str, module: str | None = None, output: str | None = None,
                            overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Kolom tanggal VARCHAR berisi tanggal MURNI (tanpa jam) dengan format apa pun dari
    STRDATE_FORMATS -- difilter dengan KESETARAAN `date_col = '<target_date diformat>'`, bukan
    rentang. Rentang string hanya benar untuk 'YYYY-MM-DD'; untuk 'DD.MM.YYYY' urutan
    leksikografisnya tidak kronologis sehingga BETWEEN/range mustahil benar (kasus tabel feed
    SAP). Kesetaraan juga bisa memakai index (seek) di kolom tanggal.
    CAST(? AS VARCHAR) WAJIB: pyodbc mengirim str Python sebagai NVARCHAR -> SQL Server
    mengonversi SELURUH kolom VARCHAR ke NVARCHAR, index tidak terpakai, tabel dipindai penuh.
    Terukur di tabel feed 64 jt baris, hasil sama: 70 detik (NVARCHAR) vs 0,2 detik (CAST).
    """
    if date_format not in STRDATE_FORMATS:
        raise ValueError(f"date_format tidak didukung: {date_format!r} (pilihan: {', '.join(STRDATE_FORMATS)})")
    output = output or f"{name}.parquet"
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    query = f"""
            SET NOCOUNT ON;
            SELECT * FROM {db}.dbo.{table} WITH (NOLOCK, NOWAIT)
            WHERE {date_col} = CAST(? AS VARCHAR(32))
        """
    return Job(name=name, query=query, params=[dt.strftime(date_format)], output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_daily(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=True, count_query=count_query_for(query),
               date_check={"column": date_col, "equals": dt.strftime(date_format)})


def bronze_daily_epoch_ms(name: str, target_date: str, *, table: str, date_col: str, dataset: str, db: str,
                          module: str | None = None, output: str | None = None,
                          overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Sama seperti bronze_daily(), tapi date_col bertipe BIGINT berisi epoch milliseconds.
    Batas hari = selisih kalender murni terhadap 1970-01-01 (pola DATEDIFF_BIG naif): device
    sumber menyimpan jam dinding WIB seolah-olah UTC. JANGAN ganti ke dt.timestamp() --
    itu mengonversi WIB->UTC (data geser ~7 jam) dan ikut timezone OS mesin yang menjalankan.
    """
    output = output or f"{name}.parquet"
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    epoch0 = _datetime(1970, 1, 1)
    start_ms = int((dt - epoch0).total_seconds() * 1000)
    end_ms = int((dt + _timedelta(days=1) - epoch0).total_seconds() * 1000)
    query = f"""
            SET NOCOUNT ON;
            SELECT * FROM {db}.dbo.{table} WITH (NOLOCK, NOWAIT)
            WHERE [{date_col}] >= ? AND [{date_col}] < ?
        """
    return Job(name=name, query=query, params=[start_ms, end_ms], output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_daily(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=True, count_query=count_query_for(query),
               date_check={"column": date_col, "min": start_ms, "max_excl": end_ms})


def bronze_daily_join(name: str, target_date: str, *, table: str, alias: str, join_table: str,
                      join_alias: str, join_on: str, date_col: str, dataset: str, db: str, join_db: str,
                      module: str | None = None, output: str | None = None,
                      overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Tabel child TANPA kolom tanggal sendiri -- JOIN ke tabel induk HANYA untuk filter
    tanggal. Kolom yang di-SELECT tetap murni {alias}.* supaya bronze tidak tercampur
    kolom tabel lain.
    """
    output = output or f"{name}.parquet"
    query = f"""
            SET NOCOUNT ON;
            SELECT {alias}.* FROM {db}.dbo.{table} {alias} WITH (NOLOCK, NOWAIT)
            INNER JOIN {join_db}.dbo.{join_table} {join_alias} WITH (NOLOCK, NOWAIT)
                ON {join_on}
            WHERE {join_alias}.{date_col} >= CAST(? AS DATE)
              AND {join_alias}.{date_col} < DATEADD(DAY, 1, CAST(? AS DATE))
        """
    return Job(name=name, query=query, params=[target_date, target_date], output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_daily(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=True, count_query=count_query_for(query))


def bronze_master(name: str, target_date: str, *, table: str, dataset: str, db: str,
                  module: str | None = None, output: str | None = None,
                  s3_file_name: str | None = None,
                  overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Master/lookup: snapshot penuh tiap run, tanpa filter tanggal.
    s3_file_name (opsional): nama file di S3 kalau beda dari output -- dipakai job yang
    sudah terlanjur upload dengan nama generik, supaya S3 key tidak berubah.
    """
    output = output or f"{name}.parquet"
    query = f"SET NOCOUNT ON; SELECT * FROM {db}.dbo.{table} WITH (NOLOCK, NOWAIT)"
    return Job(name=name, query=query, output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_master(target_date, dataset, layer="bronze", module=module,
                                file_name=s3_file_name or output),
               needs_date=False, count_query=count_query_for(query))


def bronze_arcsde_view(name: str, target_date: str, *, linked_server: str, sde_db: str, sde_version: str,
                       view: str, columns: list[str], dataset: str, module: str | None = None,
                       output: str | None = None,
                       overrides: dict[str, str] | None = None, labels: list[str] | None = None) -> Job:
    """
    Snapshot ArcSDE versioned view ("*_evw") lewat LINKED SERVER. set_current_version WAJIB
    di batch yang SAMA dengan SELECT, makanya seluruhnya dibungkus satu EXEC(N'...') AT [server].
    columns: daftar kolom EKSPLISIT (bukan SELECT *) -- kolom geometry (mis. Shape) tidak
    didukung ODBC Driver 17 ("ODBC SQL type -151 is not yet supported").
    Escaping: 2x petik tunggal ('') untuk satu ' literal di dalam EXEC(N'...').
    """
    col_list = ",\n                ".join(columns)
    query = f"""
            SET NOCOUNT ON;
            EXEC (N'
                SET FMTONLY OFF; SET NOCOUNT ON;
                EXEC {sde_db}.sde.set_current_version
                     ''{sde_version}'';
                SELECT
                {col_list}
                FROM {sde_db}.sde.{view};
            ') AT [{linked_server}];
        """
    output = output or f"{name}.parquet"
    return Job(name=name, query=query, output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_master(target_date, dataset, layer="bronze", module=module, file_name=output),
               needs_date=False)


# ---------------------------------------------------------------------------
# TEMPLATE SP DISPATCHER & SQL BEBAS
# ---------------------------------------------------------------------------

def sp_job(name: str, target_date: str, *, sp: str, function: int, output: str, needs_date: bool = True,
           overrides: dict[str, str] | None = None, labels: list[str] | None = None,
           s3: dict | None = None) -> Job:
    """
    Panggil SP dispatcher dengan @function (+ @TargetDate kalau needs_date). SP-nya
    menjalankan ETL Load (MENULIS ke SQL Server) sebelum SELECT akhir -- bukan read-only.
    """
    if needs_date:
        query = f"SET NOCOUNT ON; EXEC {sp} @function = ?, @TargetDate = ?;"
        params = [function, target_date]
    else:
        query = f"SET NOCOUNT ON; EXEC {sp} @function = ?;"
        params = [function]
    return Job(name=name, query=query, params=params, output=output,
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_key_from_spec(s3, target_date), needs_date=needs_date)


# Variabel tanggal yang boleh dipakai di params custom_sql, ditulis {"$var": "<nama>"}.
DATE_VARS: dict[str, Callable[[_datetime], object]] = {
    "target_date": lambda dt: dt.strftime("%Y-%m-%d"),
    "next_date": lambda dt: (dt + _timedelta(days=1)).strftime("%Y-%m-%d"),
}


def _resolve_param(p, dt: _datetime):
    if isinstance(p, dict):
        if p.get("$var") not in DATE_VARS:
            raise ValueError(f"param tidak dikenal: {p!r} (variabel tersedia: {', '.join(DATE_VARS)})")
        return DATE_VARS[p["$var"]](dt)
    return p


def custom_sql(name: str, target_date: str, *, query: str, output: str, params: list | None = None,
               needs_date: bool = True, overrides: dict[str, str] | None = None,
               labels: list[str] | None = None, s3: dict | None = None) -> Job:
    """
    Query bebas (SELECT atau EXEC ... diakhiri SELECT) dengan placeholder '?'. params berisi
    nilai literal atau {"$var": "target_date"} / {"$var": "next_date"}, sesuai urutan '?'.
    """
    dt = _datetime.strptime(target_date, "%Y-%m-%d")
    return Job(name=name, query=query, output=output,
               params=[_resolve_param(p, dt) for p in (params or [])],
               overrides=parse_overrides(overrides), labels=list(labels or []),
               s3_key=s3_key_from_spec(s3, target_date), needs_date=needs_date)


TEMPLATES: dict[str, Callable[..., Job]] = {
    "bronze_daily": bronze_daily,
    "bronze_daily_strdate": bronze_daily_strdate,
    "bronze_daily_strdate_eq": bronze_daily_strdate_eq,
    "bronze_daily_epoch_ms": bronze_daily_epoch_ms,
    "bronze_daily_join": bronze_daily_join,
    "bronze_master": bronze_master,
    "bronze_arcsde_view": bronze_arcsde_view,
    "sp_job": sp_job,
    "custom_sql": custom_sql,
}


def s3_target_info(template: str, args: dict) -> dict | None:
    """Ringkasan tujuan S3 sebuah job (layout/layer/module/dataset) untuk ditampilkan di Web UI."""
    if template.startswith("bronze_"):
        layout = "daily" if template.startswith("bronze_daily") else "master"
        return {"layout": layout, "layer": "bronze", "module": args.get("module"), "dataset": args.get("dataset")}
    spec = args.get("s3")
    if not spec:
        return None
    return {k: spec.get(k) for k in ("layout", "layer", "module", "dataset")}


# Irama data sumber (pipeline/dq.py): menentukan pembanding anomali volume. Disimpan per job di
# jobs.cadence; NULL = ditebak dari template lewat default_cadence().
CADENCES = ("daily", "weekly", "monthly", "snapshot")


def default_cadence(template: str, args: dict) -> str:
    """Tebakan irama dari layout S3 (harian/mingguan/master); job tanpa S3 dari needs_date."""
    info = s3_target_info(template, args)
    if info:
        return {"daily": "daily", "weekly": "weekly", "master": "snapshot"}.get(info["layout"], "daily")
    return "daily" if args.get("needs_date", True) else "snapshot"


def s3_folder(template: str, args: dict) -> str | None:
    """
    Folder dataset di S3 (tanpa partisi & nama file), mis. 'bronze/cor/master_estate'. Dua job
    dengan folder SAMA = tabrakan: Athena/Glue membaca semua file di folder itu sebagai satu tabel
    (file dobel / skema campur), walaupun nama filenya beda. Harian & mingguan dengan dataset sama
    juga bentrok (folder sama, skema partisi beda). Master aman karena prefix 'master_'.
    """
    info = s3_target_info(template, args)
    if not info:
        return None
    dataset = f"master_{info['dataset']}" if info["layout"] == "master" else info["dataset"]
    return _key(info["layer"], info["module"], dataset)


def s3_list_prefix(template: str, args: dict) -> str | None:
    """Prefix untuk me-list semua partisi dataset job di bucket (S3_PREFIX + folder + '/'), mis. inventaris S3."""
    folder = s3_folder(template, args)
    return _key(S3_PREFIX, folder) + "/" if folder else None


def build_job(name: str, template: str, args: dict, target_date: str) -> Job:
    """Satu baris database job -> objek Job untuk satu tanggal proses."""
    if template not in TEMPLATES:
        raise ValueError(f"[{name}] template tidak dikenal: {template!r}")
    try:
        job = TEMPLATES[template](name, target_date, **args)
    except TypeError as e:  # argumen kurang/berlebih dibanding signature template
        raise ValueError(f"[{name}] argumen untuk template {template!r} tidak cocok: {e}") from None
    # Semua job ber-layout S3 master (bronze & gold) memakai snapshot berbasis perubahan (pipeline/snapshots.py).
    if job.s3_key is not None and (s3_target_info(template, args) or {}).get("layout") == "master":
        job.change_snapshot = True
    return job
