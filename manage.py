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
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from pipeline import run_store
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
    args = p.parse_args()

    try:
        db = run_store.open_db()
    except JobStoreError as e:
        print(f"ERROR: {e}")
        return 1
    init_db(db)
    if args.cmd == "accept-schema":
        return accept_schema(db, args.job)
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


if __name__ == "__main__":
    sys.exit(main())
