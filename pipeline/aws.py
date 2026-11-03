"""
KLIEN AWS TERPUSAT untuk integrasi BARU: Bedrock Knowledge Base (bedrock-agent) dan pembacaan S3 jalur dokumen.

Aturan kredensial (PRD v1.1 Fase 4/10, keputusan "A"): klien di modul ini memakai IAM role lebih dulu --
AWS_PROFILE (profile di AWS_CONFIG_FILE yang meng-assume role, README §11). Hanya kalau AWS_PROFILE kosong
dipakai access key (PC dev). Ini SENGAJA kebalikan dari export.make_s3_client (klien S3 bronze: access key
menang). Klien S3 bronze tidak disentuh, jadi upload bronze & hapus snapshot master tetap memakai kredensial
lamanya, sementara Fase 10 bisa memakai role GIS04 yang belum punya izin tulis bronze/*.

Pesan error ke UI/log = pesan sendiri per kode error (error_message), BUKAN pesan asli AWS: pesan asli
memuat ARN role, nomor akun, dan nama bucket.
"""

from __future__ import annotations

import threading

import boto3
from botocore.config import Config

from pipeline.config import AWS_ACCESS_KEY_ID, AWS_PROFILE, AWS_REGION, AWS_SECRET_ACCESS_KEY, KB_DOCS_PREFIX, S3_BUCKET_NAME

_CONFIG = Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=5, read_timeout=30)
_lock = threading.Lock()
_session = None
_clients: dict[str, object] = {}


class AwsNotConfigured(Exception):
    """Tidak ada AWS_PROFILE maupun access key di .env."""


def auth_mode() -> str:
    """'role' (AWS_PROFILE) | 'access_key' | 'none'."""
    if AWS_PROFILE:
        return "role"
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        return "access_key"
    return "none"


def configured() -> bool:
    return auth_mode() != "none"


def reset() -> None:
    """Buang sesi & klien tersimpan (uji / setelah konfigurasi berubah)."""
    global _session
    with _lock:
        _session = None
        _clients.clear()


def client(service: str):
    """
    Klien boto3 untuk `service` ('bedrock-agent', 's3', 'sts'), dipakai ulang antar pemanggil (thread-safe).
    Kredensial sementara IAM role diperbarui otomatis oleh botocore. Raise AwsNotConfigured bila belum diatur.
    """
    global _session
    with _lock:
        if service in _clients:
            return _clients[service]
        mode = auth_mode()
        if mode == "none":
            raise AwsNotConfigured("AWS_PROFILE (IAM role) atau access key belum diisi di .env server.")
        if _session is None:
            if mode == "role":
                _session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
            else:
                _session = boto3.Session(aws_access_key_id=AWS_ACCESS_KEY_ID,
                                         aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
        _clients[service] = _session.client(service, config=_CONFIG)
        return _clients[service]


# ---------------------------------------------------------------------------
# Error -> pesan aplikasi
# ---------------------------------------------------------------------------

_ROLE_HINT = "cek AWS_CONFIG_FILE, AWS_PROFILE, dan AWS_EC2_METADATA_SERVICE_ENDPOINT"
_MESSAGES = {
    "AccessDeniedException": "Akses ditolak AWS: role/kredensial aplikasi belum punya izin untuk aksi ini.",
    "AccessDenied": "Akses ditolak AWS: role/kredensial aplikasi belum punya izin untuk aksi ini.",
    "AllAccessDisabled": "Akses ditolak AWS: role/kredensial aplikasi belum punya izin untuk aksi ini.",
    "UnauthorizedOperation": "Akses ditolak AWS: role/kredensial aplikasi belum punya izin untuk aksi ini.",
    "ExpiredToken": "Kredensial AWS kedaluwarsa. Mode IAM role: " + _ROLE_HINT + ".",
    "ExpiredTokenException": "Kredensial AWS kedaluwarsa. Mode IAM role: " + _ROLE_HINT + ".",
    "UnrecognizedClientException": "Kredensial AWS tidak dikenal (kunci salah atau sudah dicabut).",
    "InvalidAccessKeyId": "Kunci akses AWS di .env tidak dikenal.",
    "SignatureDoesNotMatch": "Kunci rahasia AWS di .env tidak cocok.",
    "CredentialRetrievalError": "Kredensial IAM role gagal diambil: " + _ROLE_HINT + ".",
    "NoCredentialsError": "Kredensial IAM role gagal diambil: " + _ROLE_HINT + ".",
    "ProfileNotFound": "Profile AWS_PROFILE tidak ada di AWS_CONFIG_FILE.",
    "LoginRefreshRequired": "Sesi login AWS profile habis (jalankan 'aws login' di mesin dev).",
    "MissingDependencyException": "Profile ini butuh paket botocore[crt] (hanya untuk mesin dev; server memakai role).",
    "AwsNotConfigured": "AWS_PROFILE (IAM role) atau access key belum diisi di .env server.",
    "ResourceNotFoundException": "Knowledge Base atau data source tidak ditemukan (periksa ID-nya).",
    "NoSuchBucket": "Bucket S3 di .env tidak ditemukan.",
    "NoSuchKey": "Objek S3 tidak ditemukan.",
    "ThrottlingException": "AWS membatasi permintaan (throttling); akan dicoba lagi otomatis.",
    "TooManyRequestsException": "AWS membatasi permintaan (throttling); akan dicoba lagi otomatis.",
    "ConflictException": "Bedrock menolak karena sedang ada proses lain pada data source ini (mis. ingestion yang masih berjalan).",
    "ServiceQuotaExceededException": "Batas layanan Bedrock tercapai; coba lagi nanti.",
    "ValidationException": "Permintaan ditolak Bedrock (isian tidak valid).",
    "EndpointConnectionError": "Endpoint AWS tidak terjangkau dari server (jaringan keluar HTTPS 443 / DNS).",
    "ConnectTimeoutError": "Endpoint AWS tidak terjangkau dari server (waktu habis).",
    "ReadTimeoutError": "AWS tidak merespons tepat waktu.",
    "ConnectionClosedError": "Koneksi ke AWS terputus.",
}
# Kode yang menandakan SEMUA panggilan pasti gagal (kredensial / jaringan) -- siklus pemantauan dihentikan.
FATAL = {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidAccessKeyId",
         "SignatureDoesNotMatch", "CredentialRetrievalError", "NoCredentialsError", "ProfileNotFound",
         "LoginRefreshRequired", "MissingDependencyException", "AwsNotConfigured", "EndpointConnectionError",
         "ConnectTimeoutError"}
DENIED = {"AccessDeniedException", "AccessDenied", "AllAccessDisabled", "UnauthorizedOperation"}


def error_code(e: BaseException) -> str:
    return (getattr(e, "response", None) or {}).get("Error", {}).get("Code") or type(e).__name__


def error_message(e: BaseException) -> str:
    code = error_code(e)
    return _MESSAGES.get(code, f"Gagal memanggil AWS ({code}).")


# ---------------------------------------------------------------------------
# Uji koneksi (Pengaturan)
# ---------------------------------------------------------------------------

def _probe(key: str, label: str, fn) -> dict:
    try:
        return {"key": key, "label": label, "status": "ok", "message": fn() or "Terhubung."}
    except Exception as e:  # noqa: BLE001 -- hasil uji = status, bukan exception
        code = error_code(e)
        status = "denied" if code in DENIED else "unreachable" if code in ("EndpointConnectionError", "ConnectTimeoutError") else "error"
        return {"key": key, "label": label, "status": status, "message": error_message(e)}


def check_connections(kb_ids: list[str]) -> list[dict]:
    """
    Uji koneksi per layanan: kredensial (STS, tanpa izin khusus), Bedrock Knowledge Base (GetKnowledgeBase pada KB
    terdaftar), dan S3 jalur dokumen (LIST prefix KB_DOCS_PREFIX). Tiap hasil: {key, label, status, message},
    status = ok | denied | unreachable | error | skipped. Tidak pernah melempar error.
    """
    if not configured():
        msg = "AWS_PROFILE (IAM role) atau access key belum diisi di .env server."
        return [{"key": k, "label": lb, "status": "error", "message": msg}
                for k, lb in (("credentials", "Kredensial AWS"), ("bedrock", "Bedrock Knowledge Base"), ("s3_docs", "S3 jalur dokumen"))]
    out = [_probe("credentials", "Kredensial AWS", lambda: client("sts").get_caller_identity() and f"Valid (mode {auth_mode()}).")]
    if kb_ids:
        results = [_probe("bedrock", "Bedrock Knowledge Base", lambda kb=kb: client("bedrock-agent").get_knowledge_base(
            knowledgeBaseId=kb) and "Terhubung.") for kb in kb_ids[:3]]
        bad = [r for r in results if r["status"] != "ok"]
        out.append(bad[0] if bad else results[0])
    else:
        out.append({"key": "bedrock", "label": "Bedrock Knowledge Base", "status": "skipped",
                    "message": "Belum ada target Knowledge Base terdaftar untuk diuji."})
    if S3_BUCKET_NAME:
        out.append(_probe("s3_docs", "S3 jalur dokumen", lambda: client("s3").list_objects_v2(
            Bucket=S3_BUCKET_NAME, Prefix=KB_DOCS_PREFIX, MaxKeys=1) and "LIST berhasil."))
    else:
        out.append({"key": "s3_docs", "label": "S3 jalur dokumen", "status": "skipped",
                    "message": "S3_BUCKET_NAME belum diisi di .env."})
    return out
