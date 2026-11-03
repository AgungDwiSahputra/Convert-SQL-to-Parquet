"""
Cek jaringan dari mesin / container ini ke semua layanan yang dipakai aplikasi: DNS + koneksi TCP
ke SQL Server, portal SSO, SMTP, S3, dan Bedrock Knowledge Base (+ STS & endpoint metadata EC2 kalau memakai IAM role lewat
AWS_PROFILE). Tidak login dan tidak menampilkan kredensial -- hanya nama host, IP hasil resolve, dan
berhasil/tidaknya koneksi. Host dibaca dari environment / .env.

    python scripts/check_network.py
    docker run --rm --env-file .env parquet-exporter python scripts\\check_network.py

Exit 1 kalau ada yang gagal. DNS gagal di Windows container -> build ulang image dengan
--build-arg HOSTS_ENTRIES (README §11).
"""

from __future__ import annotations

import os
import socket
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline.config  # noqa: E402,F401  -- memuat .env folder proyek kalau ada


def sql_target() -> tuple[str, int] | None:
    raw = os.environ.get("SQLSERVER_HOST", "").strip()
    if not raw:
        return None
    host, _, port = raw.partition(",")
    host = host.split("\\")[0]          # nama instance (HOST\INSTANCE) -> host saja
    return host, int(port or 1433)      # instance bernama tanpa port: cek manual (SQL Browser UDP 1434)


def url_target(url: str) -> tuple[str, int] | None:
    u = urlparse(url)
    if not u.hostname:
        return None
    return u.hostname, u.port or (443 if u.scheme == "https" else 80)


def check(label: str, target: tuple[str, int] | None) -> bool:
    if target is None:
        print(f"[lewati] {label}: belum diisi di .env")
        return True
    host, port = target
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        print(f"[GAGAL ] {label}: DNS {host} tidak bisa di-resolve ({e})")
        return False
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except OSError as e:
        print(f"[GAGAL ] {label}: {host} -> {', '.join(ips)} | TCP {port} gagal ({e})")
        return False
    print(f"[ OK   ] {label}: {host} -> {', '.join(ips)} | TCP {port} terbuka")
    return True


def main() -> int:
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    region = os.environ.get("AWS_REGION", "us-east-1").strip()
    targets = [
        ("SQL Server (SQLSERVER_HOST)", sql_target()),
        ("Portal SSO (SSO_BASE_URL)", url_target(os.environ.get("SSO_BASE_URL", ""))),
        ("SMTP (SMTP_HOST)", (smtp_host, int(os.environ.get("SMTP_PORT", "587"))) if smtp_host else None),
        ("S3 (AWS_REGION)", (f"s3.{region}.amazonaws.com", 443)),
        ("Bedrock Knowledge Base (bedrock-agent, AWS_REGION)", (f"bedrock-agent.{region}.amazonaws.com", 443)),
    ]
    if os.environ.get("AWS_PROFILE", "").strip():   # IAM role: kredensial instance dari metadata, lalu assume role
        metadata = os.environ.get("AWS_EC2_METADATA_SERVICE_ENDPOINT", "").strip() or "http://169.254.169.254"
        targets += [
            ("Metadata EC2 (AWS_EC2_METADATA_SERVICE_ENDPOINT)", url_target(metadata)),
            ("STS (AWS_REGION)", (f"sts.{region}.amazonaws.com", 443)),
        ]
    results = [check(label, t) for label, t in targets]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
