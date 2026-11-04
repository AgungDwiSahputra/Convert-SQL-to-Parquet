#!/usr/bin/env python3
"""
Kelola user Web UI dari command line -- terutama untuk membuat ADMIN PERTAMA. Setelah itu
user lain bisa dikelola admin lewat halaman "Users" di Web UI.

Login tetap lewat SSO; yang diatur di sini hanya SIAPA yang boleh masuk dan role-nya:
    viewer   : melihat daftar job, riwayat run, log
    operator : + menjalankan / membatalkan / mengulang run
    admin    : + mengelola user

    python manage.py add-user nama.admin --role admin
    python manage.py set-role budi.santoso operator
    python manage.py deactivate budi.santoso
    python manage.py activate budi.santoso
    python manage.py list-users

Data quality: skema acuan job dilepas supaya perubahan skema sumber yang DISENGAJA tidak terus ditahan
(run berikutnya yang dipublikasikan menjadi acuan baru -- lihat pipeline/dq.py):
    python manage.py accept-schema bronze_xxx

Inventaris S3: pindai sekarang tanggal data yang ada di S3 per job aktif (hanya membaca S3; normalnya dijalankan
worker sekali sehari -- lihat pipeline/s3_inventory.py):
    python manage.py scan-s3

Nama tampilan job (label ramah non-teknis; identifier `name` TIDAK berubah): impor dari CSV berkolom `name` dan `display_name`
(pemisah ';' atau ','; UTF-8, boleh dari Excel). Tanpa --apply hanya menampilkan rencana perubahan. Sel kosong = tidak diubah,
'-' = kosongkan (kembali ke label otomatis). Semua tercatat di riwayat job (action 'rename'):
    python manage.py set-display-names nama-tampilan-job.csv
    python manage.py set-display-names nama-tampilan-job.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys

from pipeline import run_store, s3_inventory
from pipeline.job_store import JobStoreError, get_schema_baseline, init_db, save_schema_baseline, utc_now

ROLES = ("viewer", "operator", "admin")


def main() -> int:
    p = argparse.ArgumentParser(description="Kelola user Web UI.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user", help="daftarkan username SSO")
    a.add_argument("username")
    a.add_argument("--role", choices=ROLES, default="viewer")
    s = sub.add_parser("set-role", help="ubah role user")
    s.add_argument("username")
    s.add_argument("role", choices=ROLES)
    for name in ("activate", "deactivate"):
        sub.add_parser(name, help=f"{name} user").add_argument("username")
    sub.add_parser("list-users", help="tampilkan semua user")
    sub.add_parser("accept-schema", help="lepas skema acuan DQ sebuah job").add_argument("job")
    sub.add_parser("scan-s3", help="pindai inventaris S3 sekarang")
    d = sub.add_parser("set-display-names", help="impor nama tampilan job dari CSV (name;display_name)")
    d.add_argument("csv_file")
    d.add_argument("--apply", action="store_true", help="tulis perubahan (tanpa ini hanya rencana)")
    args = p.parse_args()

    try:
        db = run_store.open_db()
    except JobStoreError as e:
        print(f"ERROR: {e}")
        return 1
    init_db(db)
    if args.cmd == "accept-schema":
        return accept_schema(db, args.job)
    if args.cmd == "scan-s3":
        return scan_s3(db)
    if args.cmd == "set-display-names":
        return set_display_names_cmd(db, args.csv_file, args.apply)
    username = getattr(args, "username", "").strip().lower()

    if args.cmd == "add-user":
        try:
            db.execute("INSERT INTO users (username, role, active, created_at, created_by) VALUES (?, ?, 1, ?, ?)",
                       (username, args.role, utc_now(), "manage.py"))
        except sqlite3.IntegrityError:
            print(f"ERROR: user '{username}' sudah ada. Pakai set-role untuk mengubah role.")
            return 1
        print(f"User '{username}' ditambahkan dengan role {args.role}.")
    elif args.cmd in ("set-role", "activate", "deactivate"):
        if args.cmd == "set-role":
            cur = db.execute("UPDATE users SET role = ? WHERE username = ?", (args.role, username))
        else:
            cur = db.execute("UPDATE users SET active = ? WHERE username = ?", (int(args.cmd == "activate"), username))
            if args.cmd == "deactivate":
                db.execute("DELETE FROM sessions WHERE username = ?", (username,))
        if cur.rowcount == 0:
            print(f"ERROR: user '{username}' tidak ditemukan.")
            return 1
        print(f"User '{username}': {args.cmd} OK.")
    else:
        rows = db.execute("SELECT username, role, active, full_name, last_login_at FROM users "
                          "ORDER BY active DESC, username").fetchall()
        for r in rows:
            print(f"  {r['username']:<28} {r['role']:<9} {'aktif' if r['active'] else 'NONAKTIF':<9} "
                  f"{r['full_name'] or '-':<30} login terakhir: {r['last_login_at'] or '-'}")
        if not rows:
            print("  (belum ada user -- tambahkan admin pertama: python manage.py add-user <username> --role admin)")
    return 0


def accept_schema(db: sqlite3.Connection, job: str) -> int:
    if db.execute("SELECT 1 FROM jobs WHERE name = ?", (job,)).fetchone() is None:
        print(f"ERROR: job '{job}' tidak ditemukan.")
        return 1
    old = get_schema_baseline(job, db)
    save_schema_baseline(job, None, db)
    print(f"Skema acuan '{job}' dilepas ({len(old) if old else 0} kolom). "
          "Run berikutnya yang dipublikasikan ke S3 menjadi acuan baru.")
    return 0


def scan_s3(db: sqlite3.Connection) -> int:
    from pipeline.export import make_s3_client   # impor berat (pyodbc/pyarrow) hanya untuk perintah ini
    r = s3_inventory.scan(db, lambda: make_s3_client(False), by="manage.py")
    print(f"Inventaris S3: {r['jobs']} dari {r['targets']} job dipindai, {r['dates']} tanggal tercatat.")
    for e in r["errors"]:
        print(f"  {e['job_name']}: {e['error']}")
    if r["error"]:
        print(f"ERROR: {r['error']}")
    return 1 if r["error"] or r["errors"] else 0


def _read_display_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=";,")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if rows and not {"name", "display_name"} <= set(rows[0]):
        raise ValueError("CSV wajib punya kolom 'name' dan 'display_name'.")
    return rows


def set_display_names_cmd(db: sqlite3.Connection, path: str, apply: bool) -> int:
    from pipeline.job_store import JobRow, load_job_rows, set_display_names
    from pipeline.job_validation import validate_display_name
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # konsol cp1252: nama tampilan bebas karakter tidak boleh membuat crash
    try:
        entries = _read_display_csv(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"ERROR: {e}")
        return 1
    current = {r.name: r for r in load_job_rows(enabled_only=False)}
    wanted: dict[str, str | None] = {}
    errors: list[str] = []
    for i, e in enumerate(entries, start=2):            # baris 1 = header
        name, value = (e.get("name") or "").strip(), " ".join((e.get("display_name") or "").split())
        if not name or not value:
            continue                                    # sel kosong = tidak diubah
        if name not in current:
            errors.append(f"baris {i}: job '{name}' tidak ada")
        elif name in wanted:
            errors.append(f"baris {i}: job '{name}' muncul dua kali")
        else:
            wanted[name] = None if value == "-" else value
    # Validasi terhadap KONDISI AKHIR (semua perubahan diterapkan) -- tukar nama antar-job tetap sah.
    final = [JobRow(**{**r.__dict__, "display_name": wanted.get(r.name, r.display_name)}) for r in current.values()]
    for name, value in wanted.items():
        msg = validate_display_name(value, final, name)
        if msg:
            errors.append(f"{name}: {msg}")
    changes = [(current[n], v) for n, v in wanted.items() if current[n].display_name != v]
    for old, value in changes:
        print(f"  {old.name:<48} {old.display_name or '(otomatis: ' + old.label + ')'}  ->  {value or '(otomatis)'}")
    print(f"{len(changes)} berubah, {len(wanted) - len(changes)} sudah sama, {len(current) - len(wanted)} job tidak diubah.")
    if errors:
        print("ERROR (tidak ada yang ditulis):")
        for m in errors:
            print(f"  {m}")
        return 1
    if not apply:
        print("Belum ditulis -- jalankan ulang dengan --apply untuk menyimpan.")
        return 0
    if changes:
        set_display_names(db, changes, "manage.py")
    print(f"Tersimpan: {len(changes)} nama tampilan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
