#!/usr/bin/env python3
"""
Migrasi daftar job: jobs.py (kode Python) -> database job (SQLite, lihat job_store.py),
plus PARITY CHECK yang membuktikan hasilnya identik.

Cara kerja:
  1. jobs.py dibedah lewat AST (list job di dalam build_jobs() TIDAK dieksekusi untuk
     migrasi). Tiap pemanggilan helper di `return [...]` dipetakan ke template di
     job_templates.py; argumennya di-resolve jadi nilai EKSPLISIT -- default helper lama
     (mis. db="...", layer, nama file S3) ikut ditulis ke database, supaya path S3 dan
     query tidak bergantung pada default yang bisa berubah.
  2. Entri yang DI-COMMENT ikut diimpor sebagai job NONAKTIF (enabled=0). Komentar prosa
     yang menempel tepat di atas tiap entri disimpan ke kolom notes.
  3. PARITY: untuk beberapa tanggal uji, Job hasil database dibandingkan dengan Job hasil
     build_jobs() jobs.py asli -- query, params, output, overrides, labels, needs_date,
     dan S3 key harus identik byte-per-byte, urutan job aktif juga harus sama. Job
     nonaktif dibandingkan dengan salinan jobs.py yang entri-nya di-uncomment.
     Satu saja beda -> database TIDAK ditulis.

Pemakaian:
    python migrate_jobs.py                  # isi database pertama kali (setelah parity lolos)
    python migrate_jobs.py --dry-run        # analisis + parity saja, tanpa menulis
    python migrate_jobs.py --replace        # timpa isi database (sinkron ulang dari jobs.py)
    python migrate_jobs.py --check          # bandingkan ISI DATABASE saat ini dengan jobs.py
    python migrate_jobs.py --check --date 2026-07-20 2024-02-29   # tanggal uji sendiri

Exit code: 0 sukses/identik, 2 ada beda parity, 1 error (jobs.py tidak bisa dibaca, dsb.)
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
import sys
import types
from collections import Counter
from dataclasses import dataclass

import pyarrow as pa

from config import JOBS_DB, S3_PREFIX
from job_store import JobRow, JobStoreError, build_jobs, connect, default_target_date, init_db, \
    load_job_rows, replace_all_jobs, ui_edit_summary
from job_templates import parse_arrow_type

# Helper lama di jobs.py -> nama template di job_templates.py.
HELPER_TO_TEMPLATE = {
    "bronze_daily": "bronze_daily",
    "bronze_daily_strdate": "bronze_daily_strdate",
    "bronze_daily_epoch_ms": "bronze_daily_epoch_ms",
    "bronze_daily_join": "bronze_daily_join",
    "bronze_master": "bronze_master",
    "bronze_awm_evw": "bronze_arcsde_view",
    "sp_job": "sp_job",
    "Job": "custom_sql",
}
S3_HELPERS = {"s3_daily": "daily", "s3_weekly": "weekly", "s3_master": "master"}

# Signature dataclass Job (job.py), untuk entri Job(...) mentah di jobs.py.
_JOB_SIGNATURE = (["name", "query", "output", "params", "overrides", "labels", "s3_key", "needs_date"],
                  {"params": [], "overrides": {}, "labels": [], "s3_key": None, "needs_date": True})

# Baris komentar yang MUNGKIN awal entri job ter-comment: "nama(" diikuti string atau akhir
# baris. Bukan sekadar "nama(" -- prosa seperti "bronze_daily_strdate() menulis ..." ditolak.
_CODE_START = re.compile(r"^[A-Za-z_]\w*\(\s*(?:[\"']|$)")
_MAX_COMMENTED_LINES = 60

# Baris pertama hasil query palsu untuk memanggil s3_key saat parity (s3_weekly membacanya).
_SAMPLE_FIRST_ROW = {"week_year": 2026, "week_month": 7, "week_of_month": 3}


class MigrationError(Exception):
    pass


@dataclass
class Entry:
    call: ast.Call
    enabled: bool       # False = entri di-comment di jobs.py
    first_line: int     # nomor baris 1-based di jobs.py
    last_line: int
    notes: str | None


@dataclass
class Analysis:
    path: str
    src: str
    module_consts: dict
    pairs: list[tuple[Entry, JobRow]]   # entri yang berhasil dikonversi, urut sesuai file
    warnings: list[str]

    @property
    def rows(self) -> list[JobRow]:
        return [row for _, row in self.pairs]


# ---------------------------------------------------------------------------
# BEDAH jobs.py
# ---------------------------------------------------------------------------

def _strip_hash(text: str) -> str:
    """'# isi' -> 'isi' (satu '#' dan satu spasi setelahnya dibuang)."""
    text = text[1:]
    return (text[1:] if text.startswith(" ") else text).rstrip()


def _uncomment(line: str) -> str:
    """'        # bronze_x(...)' -> '        bronze_x(...)' (indentasi dipertahankan)."""
    return re.sub(r"^(\s*)# ?", r"\1", line, count=1)


def _parse_commented_call(lines: list[str], start: int, stop: int) -> tuple[ast.Call, int] | None:
    """Coba baca entri ter-comment mulai baris `start`: kumpulkan baris komentar sampai bisa di-parse utuh."""
    if not _CODE_START.match(_strip_hash(lines[start - 1].strip())):
        return None
    buf = []
    for ln in range(start, min(stop, start + _MAX_COMMENTED_LINES)):
        text = lines[ln - 1].strip()
        if not text.startswith("#"):
            return None
        buf.append(_strip_hash(text))
        try:
            tree = ast.parse("[\n" + "\n".join(buf) + "\n]", mode="eval")
        except SyntaxError:
            continue
        elts = tree.body.elts
        return (elts[0], ln) if len(elts) == 1 and isinstance(elts[0], ast.Call) else None
    return None


def _scan_entries(lines: list[str], job_list: ast.List) -> list[Entry]:
    """
    Jalan baris demi baris di dalam `return [...]`: entri aktif diambil dari AST, entri
    ter-comment dikenali dari teksnya. Komentar prosa yang menempel di atas sebuah entri
    (tanpa diselingi baris kosong) jadi catatan entri itu.
    """
    active = {elt.lineno: elt for elt in job_list.elts}
    entries: list[Entry] = []
    notes: list[str] = []
    ln = job_list.lineno + 1
    while ln < job_list.end_lineno:
        if ln in active:
            elt = active[ln]
            if not isinstance(elt, ast.Call):
                raise MigrationError(f"jobs.py:{ln}: entri list job bukan pemanggilan fungsi")
            entries.append(Entry(elt, True, ln, elt.end_lineno, "\n".join(notes).strip() or None))
            notes = []
            ln = elt.end_lineno + 1
            continue
        text = lines[ln - 1].strip()
        if not text:
            notes = []
        elif text.startswith("#"):
            commented = _parse_commented_call(lines, ln, job_list.end_lineno)
            if commented:
                call, last = commented
                entries.append(Entry(call, False, ln, last, "\n".join(notes).strip() or None))
                notes = []
                ln = last + 1
                continue
            notes.append(_strip_hash(text))
        else:
            raise MigrationError(f"jobs.py:{ln}: baris tidak dikenali di dalam list job: {text[:70]}")
        ln += 1
    return entries


def _make_evaluator(consts: dict, s3_sigs: dict):
    """Evaluator nilai argumen: literal, konstanta modul jobs.py, target_date, s3_*(...), pa.<tipe>(...)."""
    def ev(node: ast.AST):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.Dict) and None not in node.keys:
            return {ev(k): ev(v) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.Name):
            if node.id == "target_date":
                return {"$var": "target_date"}
            if node.id in consts:
                return consts[node.id]
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in S3_HELPERS:
                params, defaults = s3_sigs[f.id]
                return {"layout": S3_HELPERS[f.id], **_bind(f.id, params, defaults, node, ev)}
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "pa"
                    and not node.keywords):
                type_str = str(getattr(pa, f.attr)(*(ev(a) for a in node.args)))
                parse_arrow_type(type_str)  # harus bisa dibaca balik oleh template
                return type_str
        raise MigrationError(f"ekspresi tidak didukung untuk migrasi otomatis: {ast.unparse(node)[:80]}")
    return ev


def _signature(fn: ast.FunctionDef, ev) -> tuple[list[str], dict]:
    a = fn.args
    params = [x.arg for x in a.posonlyargs + a.args]
    defaults = dict(zip(params[len(params) - len(a.defaults):], (ev(d) for d in a.defaults)))
    for x, d in zip(a.kwonlyargs, a.kw_defaults):
        params.append(x.arg)
        if d is not None:
            defaults[x.arg] = ev(d)
    return params, defaults


def _bind(fn_name: str, params: list[str], defaults: dict, call: ast.Call, ev) -> dict:
    """Petakan argumen pemanggilan ke nama parameter helper; parameter yang tidak diisi = default helper."""
    if len(call.args) > len(params):
        raise MigrationError(f"{fn_name}(): argumen posisi terlalu banyak")
    values = {p: ev(node) for p, node in zip(params, call.args)}
    for kw in call.keywords:
        if kw.arg is None or kw.arg not in params or kw.arg in values:
            raise MigrationError(f"{fn_name}(): argumen {kw.arg!r} tidak valid/duplikat")
        values[kw.arg] = ev(kw.value)
    for p in params:
        if p not in values:
            if p not in defaults:
                raise MigrationError(f"{fn_name}(): argumen wajib {p!r} tidak diisi")
            values[p] = defaults[p]
    return {p: values[p] for p in params}


def _arcsde_constants(fn: ast.FunctionDef, lines: list[str]) -> dict:
    """
    Helper lama bronze_awm_evw() menulis linked server, database SDE, dan versi ArcSDE
    LANGSUNG di query-nya (bukan parameter). Template bronze_arcsde_view menerimanya sebagai
    argumen, jadi nilainya diambil dari teks query helper lama (docstring dilewati).
    """
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(lines[body[0].lineno - 1:fn.end_lineno])
    m_ver = re.search(r"EXEC\s+(\S+?)\.sde\.set_current_version\s+''(.*?)''", code)
    m_srv = re.search(r"'\)\s+AT\s+\[([^\]]+)\]", code)
    if not (m_ver and m_srv):
        raise MigrationError(f"{fn.name}(): pola set_current_version / AT [linked server] tidak dikenali")
    return {"linked_server": m_srv[1], "sde_db": m_ver[1], "sde_version": m_ver[2]}


def _to_job_row(entry: Entry, sort_order: int, ev, helper_sigs: dict, helper_fns: dict,
                consts: dict, lines: list[str]) -> JobRow:
    f = entry.call.func
    helper = f.id if isinstance(f, ast.Name) else ast.unparse(f)
    if helper not in HELPER_TO_TEMPLATE or (helper != "Job" and helper not in helper_sigs):
        raise MigrationError(f"helper {helper}() belum punya template (tambahkan ke job_templates.py + "
                             f"HELPER_TO_TEMPLATE)")
    params, defaults = _JOB_SIGNATURE if helper == "Job" else helper_sigs[helper]
    values = _bind(helper, params, defaults, entry.call, ev)
    name = values.pop("name")

    if helper in ("Job", "sp_job"):
        s3 = values.pop("s3_key")
        if s3 is not None and not isinstance(s3, dict):
            raise MigrationError("s3_key harus dari s3_daily/s3_weekly/s3_master")
        values["s3"] = s3
        values["overrides"] = values["overrides"] or {}
        values["labels"] = values["labels"] or []
        if helper == "sp_job":
            if "SP" not in consts:
                raise MigrationError("sp_job() dipakai tapi konstanta SP tidak ada di jobs.py")
            values = {"sp": consts["SP"], **values}
    elif helper == "bronze_awm_evw":
        values = {**_arcsde_constants(helper_fns[helper], lines), **values, "output": f"{name}.parquet"}
    else:
        values["output"] = values["output"] or f"{name}.parquet"

    return JobRow(name=name, template=HELPER_TO_TEMPLATE[helper], args=values, enabled=entry.enabled,
                  sort_order=sort_order, notes=entry.notes)


def analyze(path: str) -> Analysis:
    """jobs.py -> daftar JobRow (aktif + nonaktif) beserta peringatan. Error fatal -> MigrationError."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()
    tree = ast.parse(src, filename=path)

    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)):
            consts[node.targets[0].id] = node.value.value

    build = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_jobs"), None)
    returns = [n for n in build.body if isinstance(n, ast.Return)] if build else []
    if not returns or not isinstance(returns[-1].value, ast.List):
        raise MigrationError(f"{path}: build_jobs() dengan `return [...]` tidak ditemukan")

    helper_fns = {n.name: n for n in build.body if isinstance(n, ast.FunctionDef)}
    s3_sigs: dict = {}
    ev = _make_evaluator(consts, s3_sigs)
    helper_sigs = {name: _signature(fn, ev) for name, fn in helper_fns.items()}
    s3_sigs.update({name: helper_sigs[name] for name in S3_HELPERS if name in helper_sigs})

    entries = _scan_entries(lines, returns[-1].value)
    errors, warnings = [], []
    converted: list[tuple[Entry, JobRow]] = []
    for i, entry in enumerate(entries):
        try:
            converted.append((entry, _to_job_row(entry, i + 1, ev, helper_sigs, helper_fns, consts, lines)))
        except MigrationError as e:
            msg = f"jobs.py:{entry.first_line}: {e}"
            (errors if entry.enabled else warnings).append(msg + ("" if entry.enabled else " -- entri ter-comment dilewati"))

    active_names = Counter(row.name for entry, row in converted if entry.enabled)
    errors += [f"nama job aktif dobel: {n}" for n, c in active_names.items() if c > 1]
    if errors:
        raise MigrationError("Tidak bisa migrasi:\n  " + "\n  ".join(errors))

    pairs, seen = [], set()
    for entry, row in converted:
        if not entry.enabled and row.name in seen | set(active_names):
            warnings.append(f"jobs.py:{entry.first_line}: entri ter-comment '{row.name}' dilewati -- "
                            f"namanya sudah dipakai job lain (versi lain dari job yang sama)")
            continue
        seen.add(row.name)
        pairs.append((entry, row))
    return Analysis(path, src, consts, pairs, warnings)


# ---------------------------------------------------------------------------
# PARITY CHECK
# ---------------------------------------------------------------------------

def default_check_dates() -> list[str]:
    """Tanggal default + kasus tepi: kabisat, akhir bulan/tahun, pergantian tahun."""
    return list(dict.fromkeys([default_target_date(), "2024-02-29", "2026-07-20", "2026-12-31", "2027-01-01"]))


def _exec_module(src: str, filename: str, name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__file__ = filename
    exec(compile(src, filename, "exec"), mod.__dict__)
    return mod


def _fingerprint(job) -> dict:
    return {
        "query": job.query,
        "params": list(job.params),
        "output": job.output,
        "overrides": {col: str(t) for col, t in job.overrides.items()},
        "labels": list(job.labels),
        "needs_date": job.needs_date,
        "s3_key": job.s3_key(dict(_SAMPLE_FIRST_ROW)) if job.s3_key else None,
    }


def _diff(date: str, old_job, new_job) -> list[str]:
    old_fp, new_fp = _fingerprint(old_job), _fingerprint(new_job)
    return [f"[{date}] {new_job.name}: '{field}' beda\n"
            f"      jobs.py : {old_fp[field]!r:.300}\n"
            f"      database: {new_fp[field]!r:.300}"
            for field in old_fp if old_fp[field] != new_fp[field]]


def parity(analysis: Analysis, rows: list[JobRow], dates: list[str]) -> list[str]:
    """Bandingkan Job dari `rows` dengan Job dari jobs.py untuk tiap tanggal. Return daftar beda (kosong = identik)."""
    problems = []
    if analysis.module_consts.get("S3_PREFIX", S3_PREFIX) != S3_PREFIX:
        problems.append(f"S3_PREFIX beda: jobs.py={analysis.module_consts['S3_PREFIX']!r}, "
                        f"config/.env={S3_PREFIX!r} -- set S3_PREFIX di .env sesuai jobs.py")

    original = _exec_module(analysis.src, analysis.path, "_jobs_parity_original")
    # Salinan jobs.py dengan entri ter-comment yang diimpor di-uncomment -> pembanding job nonaktif.
    lines = analysis.src.splitlines()
    for entry, _row in analysis.pairs:
        if not entry.enabled:
            for i in range(entry.first_line - 1, entry.last_line):
                lines[i] = _uncomment(lines[i])
    uncommented = _exec_module("\n".join(lines) + "\n", analysis.path, "_jobs_parity_uncommented")

    enabled = [r for r in rows if r.enabled]
    disabled = [r for r in rows if not r.enabled]
    for d in dates:
        old_jobs = original.build_jobs(d)
        new_jobs = build_jobs(d, enabled)
        old_names, new_names = [j.name for j in old_jobs], [j.name for j in new_jobs]
        if old_names != new_names:
            only_old = [n for n in old_names if n not in new_names]
            only_new = [n for n in new_names if n not in old_names]
            if only_old or only_new:
                problems.append(f"[{d}] daftar job aktif beda -- hanya di jobs.py: {only_old}, "
                                f"hanya di database: {only_new}")
            else:
                first = next(i for i, (o, n) in enumerate(zip(old_names, new_names)) if o != n)
                problems.append(f"[{d}] urutan job aktif beda mulai posisi {first + 1}: "
                                f"jobs.py={old_names[first]!r}, database={new_names[first]!r}")
        old_by_name = {j.name: j for j in old_jobs}
        full_by_name = {j.name: j for j in uncommented.build_jobs(d)}
        for job in new_jobs:
            if job.name in old_by_name:
                problems += _diff(d, old_by_name[job.name], job)
        for job in build_jobs(d, disabled):
            if job.name not in full_by_name:
                problems.append(f"[{d}] {job.name}: job nonaktif tidak punya pembanding di jobs.py")
            else:
                problems += _diff(d, full_by_name[job.name], job)
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(analysis: Analysis) -> None:
    rows = analysis.rows
    n_on = sum(r.enabled for r in rows)
    print(f"{analysis.path}: {len(rows)} job -> {n_on} aktif, {len(rows) - n_on} nonaktif (entri ter-comment)")
    stats = Counter((r.template, r.enabled) for r in rows)
    for template in sorted({t for t, _ in stats}):
        print(f"  {template:<22} aktif {stats[(template, True)]:>3}   nonaktif {stats[(template, False)]:>3}")
    for w in analysis.warnings:
        print(f"  PERINGATAN {w}")


def main() -> int:
    p = argparse.ArgumentParser(description="Migrasi daftar job jobs.py -> database job (SQLite) + parity check.")
    p.add_argument("--jobs-file", default="jobs.py", help="file jobs.py sumber (default: jobs.py)")
    p.add_argument("--db", default=JOBS_DB, help=f"file database job (default: {JOBS_DB})")
    p.add_argument("--replace", action="store_true", help="timpa database yang sudah berisi job (backup .bak dibuat)")
    p.add_argument("--discard-ui-edits", action="store_true",
                   help="izinkan --replace walau job sudah diubah lewat Web UI (perubahan itu HILANG)")
    p.add_argument("--dry-run", action="store_true", help="analisis + parity saja, tidak menulis database")
    p.add_argument("--check", action="store_true", help="bandingkan isi database SAAT INI dengan jobs.py")
    p.add_argument("--date", nargs="+", metavar="YYYY-MM-DD", help="tanggal uji parity (default: beberapa kasus tepi)")
    args = p.parse_args()
    dates = args.date or default_check_dates()

    try:
        analysis = analyze(args.jobs_file)
    except (OSError, SyntaxError, MigrationError) as e:
        print(f"ERROR: {e}")
        return 1
    _print_summary(analysis)

    if args.check:
        try:
            rows = load_job_rows(enabled_only=False, path=args.db)
        except JobStoreError as e:
            print(f"ERROR: {e}")
            return 1
        print(f"Membandingkan isi {args.db} ({len(rows)} job) dengan {args.jobs_file}...")
    else:
        rows = analysis.rows

    problems = parity(analysis, rows, dates)
    print(f"Parity {len(dates)} tanggal ({', '.join(dates)}) x {len(rows)} job: "
          f"{'IDENTIK' if not problems else f'{len(problems)} BEDA'}")
    if problems:
        for prob in problems[:50]:
            print("  " + prob)
        if len(problems) > 50:
            print(f"  ... dan {len(problems) - 50} beda lainnya")
        if not args.check:
            print("Database TIDAK ditulis.")
        else:
            db = connect(args.db)
            try:
                init_db(db)
                edits = ui_edit_summary(db)
            finally:
                db.close()
            if edits:
                print(f"Catatan: {edits['count']} perubahan job lewat Web UI tercatat -- beda dengan jobs.py "
                      f"adalah WAJAR sejak Fase 2 (jobs.py tidak lagi jadi sumber edit).")
        return 2
    if args.check or args.dry_run:
        return 0

    conn = connect(args.db, create=True)
    try:
        init_db(conn)
        existing = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if existing and not args.replace:
            print(f"ERROR: {args.db} sudah berisi {existing} job. Pakai --replace untuk menimpanya "
                  f"(sinkron ulang dari {args.jobs_file}).")
            return 1
        edits = ui_edit_summary(conn) if existing else None
        if edits and not args.discard_ui_edits:
            print(f"ERROR: daftar job sudah diubah lewat Web UI ({edits['count']} perubahan, terakhir oleh "
                  f"{edits['last_by']} pada {edits['last_at']}, job {edits['last_job']}).\n"
                  f"  --replace dari {args.jobs_file} akan MENGHAPUS perubahan itu. Sejak Fase 2 daftar job "
                  f"dikelola lewat Web UI.\n  Kalau memang mau menimpa, tambahkan --discard-ui-edits.")
            return 1
        if existing:
            # Backup API SQLite, BUKAN copy file -- ikut menyalin isi WAL yang belum di-checkpoint.
            bak = sqlite3.connect(args.db + ".bak")
            try:
                conn.backup(bak)
            finally:
                bak.close()
            print(f"Backup database lama: {args.db}.bak")
        replace_all_jobs(conn, rows)
    finally:
        conn.close()
    print(f"Database ditulis: {args.db} ({len(rows)} job)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
