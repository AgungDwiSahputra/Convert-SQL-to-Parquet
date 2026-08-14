"""
Konfigurasi koneksi & setelan global. Edit sesuai lingkungan kamu.

Kredensial DIAMBIL DARI ENVIRONMENT VARIABLE (jangan hard-code password di sini,
apalagi kalau file ini masuk Git).

Windows cmd:
    set SQLSERVER_HOST=172.21.2.xxx
    set SQLSERVER_DB=NAMA_DATABASE
    set SQLSERVER_UID=user_kamu
    set SQLSERVER_PWD=password_kamu

PowerShell:
    $env:SQLSERVER_HOST="172.21.2.xxx"   (dst.)
"""

import os

from dotenv import load_dotenv

load_dotenv()  # baca file .env di folder ini (kalau ada) ke environment variable

# --- Koneksi SQL Server ---
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={os.environ.get('SQLSERVER_HOST', 'SERVERNAME')};"
    f"DATABASE={os.environ.get('SQLSERVER_DB', 'DBNAME')};"
    f"UID={os.environ.get('SQLSERVER_UID', 'user')};"
    f"PWD={os.environ.get('SQLSERVER_PWD', 'pass')};"
    "Encrypt=no;"
    "TrustServerCertificate=yes;"
)

# --- Setelan ekspor default ---
BATCH_SIZE = 100_000        # baris per fetch; turunkan bila memori terbatas
COMPRESSION = "zstd"        # dataset AI/arsip: rasio bagus, decode cepat
OUTPUT_DIR = "output"       # folder tujuan file .parquet (dibuat otomatis)

# --- Upload ke S3 (opsional per job, lihat s3_key di jobs.py) ---
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
