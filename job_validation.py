"""
ATURAN VALIDASI JOB -- dipakai Web UI saat membuat / mengubah job.

Aturan README (bagian 5 & 7) yang dulu dijaga disiplin developer, sekarang dijaga sistem:
  - Identifier (database, tabel, kolom, alias) harus identifier SQL Server BIASA dan benar-benar
    ADA di SQL Server (dicek lewat `catalog` ke INFORMATION_SCHEMA). Template merangkai query
    dengan f-string, jadi inilah yang mencegah nilai dari form menyisipkan SQL.
  - Nama job, file output, dan folder S3 unik antar job (tabrakan = saling menimpa tanpa error).
  - Template tertentu khusus admin: bisa MENULIS ke SQL Server (sp_job), akses linked server,
    atau SQL bebas.
  - Path S3 yang sudah pernah dipakai (jobs.s3_locked_at) hanya boleh dipindah admin dengan
    konfirmasi eksplisit -- data di path lama tertinggal di S3.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Protocol

from job_store import JobRow, default_target_date
from job_templates import (
    DATE_VARS, S3_LAYOUTS, STRDATE_FORMATS, TEMPLATES, build_job, parse_arrow_type, s3_folder,
)

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}

# kind: "bronze" = dibuat lewat wizard dari tabel SQL Server; "advanced" = form bebas (admin).
TEMPLATE_SPECS = {
    "bronze_daily": {"label": "Harian — kolom tanggal datetime/date", "role": "operator", "kind": "bronze"},
    "bronze_daily_strdate": {"label": "Harian — kolom tanggal teks YYYY-MM-DD", "role": "operator", "kind": "bronze"},
    "bronze_daily_strdate_eq": {"label": "Harian — kolom tanggal teks format lain", "role": "operator", "kind": "bronze"},
    "bronze_daily_epoch_ms": {"label": "Harian — kolom tanggal epoch milidetik", "role": "operator", "kind": "bronze"},
    "bronze_daily_join": {"label": "Harian — filter tanggal lewat JOIN tabel induk", "role": "operator", "kind": "bronze"},
    "bronze_master": {"label": "Master — snapshot penuh tiap run", "role": "operator", "kind": "bronze"},
    "bronze_arcsde_view": {"label": "ArcSDE view lewat linked server", "role": "admin", "kind": "advanced"},
    "sp_job": {"label": "SP dispatcher (menjalankan ETL Load)", "role": "admin", "kind": "advanced"},
    "custom_sql": {"label": "SQL bebas", "role": "admin", "kind": "advanced"},
}
assert set(TEMPLATE_SPECS) == set(TEMPLATES), "setiap template wajib punya spesifikasi di TEMPLATE_SPECS"

# Tipe kolom tanggal (INFORMATION_SCHEMA.COLUMNS.DATA_TYPE) yang cocok per template harian.
DATETIME_TYPES = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"}
STRING_TYPES = {"varchar", "nvarchar", "char", "nchar"}
EPOCH_TYPES = {"bigint"}
DATE_COL_TYPES = {
    "bronze_daily": DATETIME_TYPES,
    "bronze_daily_join": DATETIME_TYPES,
    "bronze_daily_strdate": STRING_TYPES,
    "bronze_daily_strdate_eq": STRING_TYPES,
    "bronze_daily_epoch_ms": EPOCH_TYPES,
}

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")      # identifier SQL Server biasa, tanpa quote
JOB_NAME = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
OUTPUT = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.parquet$")
SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")              # layer / module / dataset di path S3
ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,30}$")
SP_NAME = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*){0,2}$")
LINKED_SERVER = re.compile(r"^[\w.\-]{1,128}$")
SDE_VERSION = re.compile(r"^[\w./\- ]{1,128}$")             # disisipkan di dalam ''...'' -> tanpa petik
ARCSDE_COLUMN = re.compile(r"^(\[\w+\]|\w+)(\s+AS\s+\w+)?$", re.IGNORECASE)
RESERVED_NAMES = {"new"}                                    # bentrok dengan route UI /jobs/new

# Kata kunci T-SQL -- tidak boleh dipakai sebagai identifier TANPA kurung siku (template merangkai
# sebagian identifier tanpa [...] demi parity dengan job lama).
_TSQL_RESERVED = set("""
ADD ALL ALTER AND ANY AS ASC AUTHORIZATION BACKUP BEGIN BETWEEN BREAK BROWSE BULK BY CASCADE CASE CHECK
CHECKPOINT CLOSE CLUSTERED COALESCE COLLATE COLUMN COMMIT COMPUTE CONSTRAINT CONTAINS CONTAINSTABLE CONTINUE
CONVERT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER CURSOR DATABASE DBCC
DEALLOCATE DECLARE DEFAULT DELETE DENY DESC DISK DISTINCT DISTRIBUTED DOUBLE DROP DUMP ELSE END ERRLVL ESCAPE
EXCEPT EXEC EXECUTE EXISTS EXIT EXTERNAL FETCH FILE FILLFACTOR FOR FOREIGN FREETEXT FREETEXTTABLE FROM FULL
FUNCTION GOTO GRANT GROUP HAVING HOLDLOCK IDENTITY IDENTITY_INSERT IDENTITYCOL IF IN INDEX INNER INSERT
INTERSECT INTO IS JOIN KEY KILL LEFT LIKE LINENO LOAD MERGE NATIONAL NOCHECK NONCLUSTERED NOT NULL NULLIF OF
OFF OFFSETS ON OPEN OPENDATASOURCE OPENQUERY OPENROWSET OPENXML OPTION OR ORDER OUTER OVER PERCENT PIVOT PLAN
PRECISION PRIMARY PRINT PROC PROCEDURE PUBLIC RAISERROR READ READTEXT RECONFIGURE REFERENCES REPLICATION
RESTORE RESTRICT RETURN REVERT REVOKE RIGHT ROLLBACK ROWCOUNT ROWGUIDCOL RULE SAVE SCHEMA SECURITYAUDIT SELECT
SESSION_USER SET SETUSER SHUTDOWN SOME STATISTICS SYSTEM_USER TABLE TABLESAMPLE TEXTSIZE THEN TO TOP TRAN
TRANSACTION TRIGGER TRUNCATE TRY_CONVERT TSEQUAL UNION UNIQUE UNPIVOT UPDATE UPDATETEXT USE USER VALUES
VARYING VIEW WAITFOR WHEN WHERE WHILE WITH WRITETEXT
""".split())

# Baris pertama hasil query palsu -- untuk menghitung contoh path S3 layout mingguan.
_SAMPLE_FIRST_ROW = {"week_year": 2026, "week_month": 7, "week_of_month": 3}

BIG_MASTER_ROWS = 1_000_000


class Catalog(Protocol):
    """Metadata SQL Server (implementasi nyata: webapp/catalog.py)."""

    def databases(self) -> list[str]: ...
    def tables(self, db: str) -> dict[str, dict]: ...           # nama -> {"type": "table"|"view"}
    def table_rows(self, db: str, table: str) -> int | None: ...
    def columns(self, db: str, table: str) -> dict[str, dict]: ...  # nama -> {"type", "leading_index", ...}


@dataclass
class Validation:
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    preview: dict | None = None
    s3_moved: bool = False          # path S3 berubah dari path yang terkunci (admin sudah konfirmasi)
    needs_s3_confirm: bool = False  # path S3 terkunci berubah, tapi belum dikonfirmasi

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "preview": self.preview,
                "s3_moved": self.s3_moved, "needs_s3_confirm": self.needs_s3_confirm}


def can_edit(role: str, template: str) -> bool:
    spec = TEMPLATE_SPECS.get(template)
    return bool(spec) and ROLE_RANK.get(role, 0) >= ROLE_RANK[spec["role"]]


def _find(names, wanted: str) -> str | None:
    """Pencocokan nama tanpa beda huruf besar/kecil (collation SQL Server umumnya case-insensitive)."""
    lw = wanted.lower()
    return next((n for n in names if n.lower() == lw), None)


class _Checker:
    def __init__(self, catalog: Catalog, v: Validation):
        self.catalog, self.v = catalog, v
        self.cache_error = False

    def err(self, fld: str, msg: str):
        self.v.errors.setdefault(fld, msg)

    def ident(self, fld: str, value, bracketed: bool = False) -> bool:
        if not isinstance(value, str) or not IDENT.fullmatch(value):
            self.err(fld, "Harus identifier SQL biasa: huruf/angka/underscore, diawali huruf atau underscore.")
            return False
        if not bracketed and value.upper() in _TSQL_RESERVED:
            self.err(fld, f"'{value}' adalah kata kunci T-SQL — tidak didukung template ini.")
            return False
        return True

    def database(self, fld: str, db) -> bool:
        if not self.ident(fld, db):
            return False
        try:
            if not _find(self.catalog.databases(), db):
                self.err(fld, f"Database '{db}' tidak ditemukan / tidak bisa diakses login exporter.")
                return False
        except Exception as e:  # noqa: BLE001
            self.err(fld, f"Tidak bisa memeriksa SQL Server: {e}")
            return False
        return True

    def table(self, fld: str, db, table) -> dict | None:
        if not self.ident(fld, table):
            return None
        try:
            name = _find(self.catalog.tables(db), table)
        except Exception as e:  # noqa: BLE001
            self.err(fld, f"Tidak bisa memeriksa SQL Server: {e}")
            return None
        if not name:
            self.err(fld, f"Tabel/view dbo.{table} tidak ada di database {db}.")
            return None
        try:
            rows = self.catalog.table_rows(db, name)
        except Exception:  # noqa: BLE001 -- jumlah baris hanya untuk peringatan
            rows = None
        return {**self.catalog.tables(db)[name], "rows": rows}

    def column(self, fld: str, db, table, col, types: set[str] | None = None, bracketed: bool = False) -> dict | None:
        if not self.ident(fld, col, bracketed=bracketed):
            return None
        try:
            cols = self.catalog.columns(db, table)
        except Exception as e:  # noqa: BLE001
            self.err(fld, f"Tidak bisa memeriksa SQL Server: {e}")
            return None
        name = _find(cols, col)
        if not name:
            self.err(fld, f"Kolom '{col}' tidak ada di {db}.dbo.{table}.")
            return None
        info = cols[name]
        if types and info["type"] not in types:
            self.err(fld, f"Kolom '{col}' bertipe {info['type']} — template ini butuh {', '.join(sorted(types))}.")
            return None
        return info

    def segment(self, fld: str, value, required: bool = True) -> None:
        if value in (None, ""):
            if required:
                self.err(fld, "Wajib diisi.")
            return
        if not isinstance(value, str) or not SEGMENT.fullmatch(value):
            self.err(fld, "Hanya huruf, angka, underscore, minus (tanpa spasi / garis miring).")

    def output(self, fld: str, value, required: bool = True) -> None:
        if value in (None, ""):
            if required:
                self.err(fld, "Wajib diisi.")
            return
        if not isinstance(value, str) or not OUTPUT.fullmatch(value):
            self.err(fld, "Nama file .parquet tanpa spasi/folder, mis. bronze_xxx.parquet.")

    def s3_spec(self, spec) -> None:
        if spec in (None, {}):
            return
        if not isinstance(spec, dict):
            self.err("s3", "Format tujuan S3 tidak valid.")
            return
        if spec.get("layout") not in S3_LAYOUTS:
            self.err("s3.layout", f"Pilih salah satu: {', '.join(S3_LAYOUTS)}.")
        for k in ("layer", "dataset"):
            self.segment(f"s3.{k}", spec.get(k))
        self.segment("s3.module", spec.get("module"), required=False)
        self.output("s3.file_name", spec.get("file_name"))
        unknown = set(spec) - {"layout", "layer", "module", "dataset", "file_name"}
        if unknown:
            self.err("s3", f"Field tidak dikenal: {', '.join(sorted(unknown))}.")

    def overrides(self, overrides, db=None, table=None) -> None:
        if not overrides:
            return
        if not isinstance(overrides, dict):
            self.err("overrides", "Format override tidak valid.")
            return
        for col, t in overrides.items():
            try:
                parse_arrow_type(t)
            except (ValueError, AttributeError) as e:
                self.err("overrides", f"Kolom {col}: {e}")
        if db and table:
            try:
                cols = {c.lower() for c in self.catalog.columns(db, table)}
                missing = [c for c in overrides if c.lower() not in cols]
                if missing:
                    self.v.warnings.append(f"Override untuk kolom yang tidak ada di tabel: {', '.join(missing)}.")
            except Exception:  # noqa: BLE001
                pass


def _check_template_args(template: str, args: dict, c: _Checker) -> None:
    """Field wajib / tidak dikenal berdasarkan signature fungsi template."""
    params = inspect.signature(TEMPLATES[template]).parameters
    accepted = {n for n, p in params.items() if p.kind == p.KEYWORD_ONLY}
    required = {n for n in accepted if params[n].default is inspect.Parameter.empty}
    for n in sorted(required - {k for k, v in args.items() if v not in (None, "", [])}):
        c.err(n, "Wajib diisi.")
    for n in sorted(set(args) - accepted):
        c.err(n, "Field tidak dikenal untuk template ini.")


def _check_bronze(template: str, a: dict, c: _Checker) -> None:
    v = c.v
    db_ok = c.database("db", a.get("db"))
    table_info = c.table("table", a["db"], a.get("table")) if db_ok else None
    if template in ("bronze_daily", "bronze_daily_strdate", "bronze_daily_strdate_eq", "bronze_daily_epoch_ms"):
        if table_info:
            col = c.column("date_col", a["db"], a["table"], a.get("date_col"), DATE_COL_TYPES[template],
                           bracketed=template == "bronze_daily_epoch_ms")
            if col and not col.get("leading_index") and (table_info.get("rows") or 0) > BIG_MASTER_ROWS:
                v.warnings.append(f"Kolom {a['date_col']} tidak punya index & tabel besar "
                                  f"({table_info['rows']:,} baris) — filter harian akan memindai seluruh tabel.")
        if template == "bronze_daily_strdate_eq" and a.get("date_format") not in STRDATE_FORMATS:
            c.err("date_format", "Pilih format tanggal yang didukung.")
    elif template == "bronze_daily_join":
        for f in ("alias", "join_alias"):
            if not (isinstance(a.get(f), str) and ALIAS.fullmatch(a[f])) or a[f].upper() in _TSQL_RESERVED:
                c.err(f, "Alias: huruf/angka/underscore, maks 31 karakter, bukan kata kunci T-SQL.")
        if a.get("alias") and a.get("alias") == a.get("join_alias"):
            c.err("join_alias", "Alias tabel induk harus beda dengan alias tabel utama.")
        jdb_ok = c.database("join_db", a.get("join_db"))
        jt = c.table("join_table", a["join_db"], a.get("join_table")) if jdb_ok else None
        if jt:
            c.column("date_col", a["join_db"], a["join_table"], a.get("date_col"), DATETIME_TYPES)
        m = re.fullmatch(r"\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*", a.get("join_on") or "")
        if not m:
            c.err("join_on", "Format: <alias>.<kolom> = <alias_induk>.<kolom>.")
        elif table_info and jt and "alias" not in v.errors and "join_alias" not in v.errors:
            sides = {m[1]: m[2], m[3]: m[4]}
            if set(sides) != {a["alias"], a["join_alias"]}:
                c.err("join_on", f"Harus menghubungkan {a['alias']}.<kolom> dengan {a['join_alias']}.<kolom>.")
            else:
                c.column("join_on", a["db"], a["table"], sides[a["alias"]])
                c.column("join_on", a["join_db"], a["join_table"], sides[a["join_alias"]])
    elif template == "bronze_master":
        if table_info and (table_info.get("rows") or 0) > BIG_MASTER_ROWS:
            v.warnings.append(f"Tabel {a['table']} berisi {table_info['rows']:,} baris dan akan ditarik PENUH "
                              f"setiap run (master/snapshot).")
        c.output("s3_file_name", a.get("s3_file_name"), required=False)
    c.segment("dataset", a.get("dataset"))
    c.segment("module", a.get("module"), required=False)
    c.output("output", a.get("output"))
    c.overrides(a.get("overrides"), a.get("db") if db_ok else None, a.get("table") if table_info else None)


def _check_advanced(template: str, a: dict, c: _Checker) -> None:
    if template == "bronze_arcsde_view":
        if not (isinstance(a.get("linked_server"), str) and LINKED_SERVER.fullmatch(a["linked_server"])):
            c.err("linked_server", "Nama/IP linked server: huruf, angka, titik, minus.")
        c.ident("sde_db", a.get("sde_db"))
        if not (isinstance(a.get("sde_version"), str) and SDE_VERSION.fullmatch(a["sde_version"])):
            c.err("sde_version", "Versi ArcSDE: huruf, angka, titik, garis miring, minus, spasi (tanpa petik).")
        c.ident("view", a.get("view"))
        cols = a.get("columns")
        if not isinstance(cols, list) or not cols:
            c.err("columns", "Isi minimal satu kolom.")
        else:
            bad = [x for x in cols if not (isinstance(x, str) and ARCSDE_COLUMN.fullmatch(x.strip()))]
            if bad:
                c.err("columns", f"Kolom tidak valid: {', '.join(map(str, bad[:5]))} (format: kolom, [kolom], kolom AS alias).")
        c.segment("dataset", a.get("dataset"))
        c.segment("module", a.get("module"), required=False)
        c.output("output", a.get("output"))
    elif template == "sp_job":
        if not (isinstance(a.get("sp"), str) and SP_NAME.fullmatch(a["sp"])):
            c.err("sp", "Nama SP, mis. dbo.Nama_SP.")
        if not isinstance(a.get("function"), int) or isinstance(a.get("function"), bool) or a["function"] < 0:
            c.err("function", "Bilangan bulat >= 0.")
        c.output("output", a.get("output"))
        c.s3_spec(a.get("s3"))
    elif template == "custom_sql":
        if not isinstance(a.get("query"), str) or not a["query"].strip():
            c.err("query", "Wajib diisi.")
        params = a.get("params") or []
        if not isinstance(params, list):
            c.err("params", "Harus berupa daftar.")
        else:
            for p in params:
                if isinstance(p, dict) and p.get("$var") not in DATE_VARS:
                    c.err("params", f"Variabel tidak dikenal: {p!r} (tersedia: {', '.join(DATE_VARS)}).")
                elif not isinstance(p, (dict, str, int, float, bool)) and p is not None:
                    c.err("params", f"Nilai tidak didukung: {p!r}.")
            if isinstance(a.get("query"), str) and a["query"].count("?") != len(params):
                c.v.warnings.append(f"Query berisi {a['query'].count('?')} tanda '?' tapi ada {len(params)} parameter.")
        c.output("output", a.get("output"))
        c.s3_spec(a.get("s3"))
    if template != "bronze_arcsde_view":
        c.overrides(a.get("overrides"))


def validate_job(name: str, template: str, args: dict, *, role: str, rows: list[JobRow], catalog: Catalog,
                 original: JobRow | None = None, confirm_s3_move: bool = False,
                 target_date: str | None = None) -> Validation:
    """
    Validasi draf job (create kalau original=None, edit kalau ada). `rows` = SEMUA job (untuk cek
    keunikan). Tidak menulis apa pun.
    """
    v = Validation()
    c = _Checker(catalog, v)
    target_date = target_date or default_target_date()

    if template not in TEMPLATE_SPECS:
        v.errors["template"] = "Template tidak dikenal."
        return v
    if not can_edit(role, template):
        v.errors["template"] = "Template ini khusus admin."
    if original and not can_edit(role, original.template):
        v.errors["template"] = "Job ini hanya bisa diubah admin."
    if not isinstance(args, dict):
        v.errors["args"] = "Format parameter tidak valid."
        return v

    if original is None:
        if not isinstance(name, str) or not JOB_NAME.fullmatch(name) or name in RESERVED_NAMES:
            v.errors["name"] = "Huruf kecil/angka/underscore, 3-80 karakter, diawali huruf (mis. bronze_hms_panen)."
        elif any(r.name == name for r in rows):
            v.errors["name"] = f"Nama '{name}' sudah dipakai job lain."
    else:
        name = original.name

    _check_template_args(template, args, c)
    if not v.errors.keys() - {"name", "template"}:  # field lengkap -> cek isi
        (_check_bronze if TEMPLATE_SPECS[template]["kind"] == "bronze" else _check_advanced)(template, args, c)

    if v.errors:
        return v

    try:
        job = build_job(name, template, args, target_date)
    except Exception as e:  # noqa: BLE001
        v.errors["args"] = f"Job tidak bisa dibangun: {e}"
        return v

    others = [r for r in rows if not original or r.name != original.name]
    if any(_output_of(r, target_date) == job.output for r in others):
        v.errors["output"] = f"File output '{job.output}' sudah dipakai job lain."
    folder = s3_folder(template, args)
    if folder:
        clash = next((r.name for r in others if s3_folder(r.template, r.args) == folder), None)
        if clash:
            v.errors["dataset"] = (f"Folder S3 '{folder}' sudah dipakai job {clash} — dua job di folder yang sama "
                                   f"akan terbaca sebagai satu tabel di Athena/Glue.")

    s3_key = None
    if job.s3_key:
        s3_key = job.s3_key(dict(_SAMPLE_FIRST_ROW))
    if original and original.s3_locked_at:
        old = build_job(original.name, original.template, original.args, target_date)
        old_key = old.s3_key(dict(_SAMPLE_FIRST_ROW)) if old.s3_key else None
        if old_key != s3_key:
            if ROLE_RANK.get(role, 0) < ROLE_RANK["admin"]:
                v.errors["s3"] = ("Path S3 job ini sudah dipakai sejak "
                                  f"{original.s3_locked_at[:10]} — hanya admin yang boleh memindahkannya.")
            elif not confirm_s3_move:
                v.needs_s3_confirm = True
                v.errors["s3"] = "Path S3 terkunci berubah — konfirmasi pemindahan dulu."
            else:
                v.s3_moved = True
                v.warnings.append(f"Path S3 dipindah. Data lama tetap ada di path lama ({old_key}).")

    if not template.startswith("sp_") and name.startswith("bronze_"):
        v.warnings.append('Kalau diaktifkan, job ini otomatis ikut jadwal run_exporter.bat (--only "bronze_*").')
    if folder and not (args.get("module") or (args.get("s3") or {}).get("module")):
        v.warnings.append("Modul kosong — path S3 tanpa segmen modul (sebaiknya diisi, mis. hms/iot/pzo).")

    v.preview = {
        "output": job.output,
        "needs_date": job.needs_date,
        "s3_folder": folder,
        "s3_key_example": s3_key,
        "s3_dynamic": (args.get("s3") or {}).get("layout") == "weekly",
        "example_date": target_date,
        "query": job.query,
        "params": [p if isinstance(p, (str, int, float, bool)) or p is None else str(p) for p in job.params],
    }
    return v


def _output_of(row: JobRow, target_date: str) -> str | None:
    try:
        return build_job(row.name, row.template, row.args, target_date).output
    except Exception:  # noqa: BLE001
        return None
