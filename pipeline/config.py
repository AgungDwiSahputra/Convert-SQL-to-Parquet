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

# Folder proyek (induk folder pipeline/). Semua path default di bawah relatif ke sini, BUKAN ke
# folder tempat perintah dijalankan -- hasilnya sama dari .bat, Task Scheduler, maupun terminal.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(PROJECT_DIR, ".env"))  # baca .env di folder proyek (kalau ada) ke environment variable

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
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(PROJECT_DIR, "output"))  # file .parquet lokal (dibuat otomatis)

# --- Daftar job (SQLite) ---
# Dibuat/diisi oleh migrate_jobs.py, dibaca exporter.py (lihat job_store.py). Masuk
# .gitignore -- isinya query/nama tabel/S3 key nyata milik environment ini.
JOBS_DB = os.environ.get("JOBS_DB", os.path.join(PROJECT_DIR, "jobs.db"))

# --- Upload ke S3 (opsional per job, lihat kunci S3 job di job_templates.py) ---
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
# Segmen paling luar S3 key, DI ATAS layer. "" = bronze/silver/gold langsung di root
# bucket. Mengubahnya memindahkan path SEMUA job -- jangan diubah tanpa alasan kuat.
S3_PREFIX = os.environ.get("S3_PREFIX", "")

# --- Web UI (webapp/) ---
# Base URL SSO portal, mis. https://<host>/portalpage -- endpoint /api/auth/gettoken & /api/cor/user.
SSO_BASE_URL = os.environ.get("SSO_BASE_URL", "").rstrip("/")
# Lama session login Web UI (jam). Login ulang lewat SSO setelah habis.
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "8"))
# true kalau Web UI diakses lewat HTTPS -> cookie session hanya dikirim lewat HTTPS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
# Alamat Web UI seperti dibuka user (dipakai untuk link di email notifikasi), mis. https://<server>/
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")

# --- Email notifikasi (SMTP) ---
# SEMUA nilai hanya dari environment/.env server -- tidak ada default yang berisi alamat/akun nyata.
# SMTP_SECURITY: "starttls" (default), "ssl" (SMTPS, biasanya port 465), atau "none" (tanpa enkripsi).
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
SMTP_LOCAL_HOSTNAME = os.environ.get("SMTP_LOCAL_HOSTNAME", "") or None   # nama domain klien (EHLO)

# --- Ekspor ad-hoc (download .parquet dari browser) ---
ADHOC_MAX_ROWS = int(os.environ.get("ADHOC_MAX_ROWS", "500000"))

# --- Log proses CLI & worker (logs/exporter_*.log, logs/worker_*.log) ---
LOG_DIR = os.environ.get("LOG_DIR", os.path.join(PROJECT_DIR, "logs"))

# --- Log per run Web UI (logs/runs/run_<id>.log) ---
# Nomor run berasal dari database: database lain (mis. salinan untuk uji) WAJIB memakai folder log
# lain, kalau tidak log run-nya bercampur dengan run asli yang bernomor sama.
RUN_LOG_DIR = os.environ.get("RUN_LOG_DIR", os.path.join(PROJECT_DIR, "logs", "runs"))

# --- Pemeliharaan harian (dijalankan worker.py) ---
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(PROJECT_DIR, "backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))                 # jumlah backup jobs.db yang disimpan
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "90"))   # umur maks log run & log worker
