"""
Cek runtime saat build image Docker (Dockerfile.windows): pyarrow bisa di-load (butuh Visual C++
Redistributable) dan ODBC Driver yang dipakai CONN_STR terdaftar. Exit 1 = build dihentikan --
lebih baik ketahuan saat build daripada saat run pertama di produksi.

    python scripts/check_runtime.py
"""

import sys

import pyarrow
import pyodbc

DRIVER = "ODBC Driver 17 for SQL Server"   # sama dengan CONN_STR di pipeline/config.py

drivers = pyodbc.drivers()
print(f"pyarrow {pyarrow.__version__} | pyodbc {pyodbc.version} | ODBC: {drivers}")
if DRIVER not in drivers:
    print(f"ERROR: '{DRIVER}' tidak terpasang.")
    sys.exit(1)
