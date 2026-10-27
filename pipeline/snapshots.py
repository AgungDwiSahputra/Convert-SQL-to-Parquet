"""
SNAPSHOT MASTER BERBASIS PERUBAHAN -- skema "Snapshot & SHA-256 Comparison" untuk semua job ber-layout
master (bronze_master, bronze_arcsde_view, sp_job / custom_sql dengan layout S3 master).

Folder S3 tetap .../master_<dataset>/snapshot_date=YYYY-MM-DD/<file> (tanggal = tanggal PROSES, H-1),
tapi folder baru hanya dibuat kalau data BERUBAH:

  hash sama  (5A) : upload ke folder tanggal proses terbaru, lalu HAPUS objek folder lama -- "rename"
                    (S3 tidak punya rename; upload dulu baru hapus supaya data tidak pernah hilang).
  hash beda  (5B) : snapshot baru; folder lama tetap sebagai histori.
  tanggal sama    : run ulang -> ditimpa di folder yang sama.
  tanggal lebih lama dari snapshot terakhir (backfill): histori TIDAK diubah, upload dilewati -- data
                    master dari sumber hanya mencerminkan kondisi sekarang, bukan kondisi tanggal lampau.

Arti tanggal folder = tanggal TERAKHIR versi data itu terkonfirmasi. Terbaru = MAX(snapshot_date);
kondisi pada tanggal T = folder dengan snapshot_date terkecil yang >= T. Tanggal pertama versi muncul
dicatat di master_snapshots.first_seen. Hash juga ditulis ke metadata Parquet (HASH_META_KEY).

Hash (DataHasher): SHA-256 per baris dari data setelah konversi Arrow, TANPA kolom audit _ingested_at,
diurutkan lalu di-hash bersama nama & tipe kolom -> tidak bergantung urutan baris (SELECT tanpa ORDER BY
tidak menjamin urutan), tapi baris dobel tetap terhitung.

Folder snapshot harian yang dibuat sebelum logika ini berlaku TIDAK disentuh: run pertama tiap job
membuat snapshot baru dan mencatat hash-nya; perbandingan berlaku mulai run berikutnya.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import json
from dataclasses import dataclass

import pyarrow as pa

from pipeline.job_store import latest_master_snapshot, save_master_snapshot

HASH_META_KEY = "pqx.data_sha256"


def _json_default(v):
    if isinstance(v, decimal.Decimal):
        return f"D:{v}"
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return str(v)


class DataHasher:
    """SHA-256 seluruh data yang tidak bergantung urutan baris. Diisi per batch selama streaming."""

    def __init__(self, columns: list[str], schema_signature: list):
        self._columns = columns          # kolom data (tanpa _ingested_at), urutan sesuai hasil query
        self._signature = schema_signature
        self._digests: list[bytes] = []

    def update(self, table: pa.Table) -> None:
        for row in table.select(self._columns).to_pylist():
            raw = json.dumps(list(row.values()), default=_json_default, separators=(",", ":"), ensure_ascii=False)
            self._digests.append(hashlib.sha256(raw.encode("utf-8")).digest())

    def hexdigest(self) -> str:
        h = hashlib.sha256(json.dumps(self._signature).encode("utf-8"))
        h.update(len(self._digests).to_bytes(8, "big"))
        for d in sorted(self._digests):
            h.update(d)
        return h.hexdigest()


@dataclass
class Plan:
    action: str            # new | rename | overwrite | skip_backfill
    key: str | None        # key tujuan upload (None = tidak diupload)
    old_key: str | None    # dihapus SETELAH upload berhasil
    message: str


def _folder(key: str) -> str:
    return key.rsplit("/snapshot_date=", 1)[0]


def plan(prev: dict | None, target_date: str, data_hash: str, key: str) -> Plan:
    """Tentukan tindakan untuk satu run master. prev = snapshot terakhir (master_snapshots) atau None."""
    if prev is None:
        return Plan("new", key, None, "snapshot pertama (belum ada catatan hash) -> folder baru")
    if _folder(prev["s3_key"]) != _folder(key):
        return Plan("new", key, None, "path S3 job berubah -> snapshot baru (snapshot lama tetap di path lama)")
    if target_date < prev["snapshot_date"]:
        return Plan("skip_backfill", None, None,
                    f"tanggal proses {target_date} lebih lama dari snapshot terakhir {prev['snapshot_date']}: "
                    f"histori master tidak diubah, upload dilewati (data master = kondisi sekarang)")
    if target_date == prev["snapshot_date"]:
        same = data_hash == prev["data_hash"]
        return Plan("overwrite", key, prev["s3_key"] if prev["s3_key"] != key else None,
                    "run ulang tanggal yang sama -> ditimpa" + ("" if same else " (data berubah)"))
    if data_hash == prev["data_hash"]:
        return Plan("rename", key, prev["s3_key"],
                    f"data sama dengan snapshot {prev['snapshot_date']} -> folder digeser ke {target_date}")
    return Plan("new", key, None, f"data berubah dari snapshot {prev['snapshot_date']} -> snapshot baru "
                                  f"(snapshot lama tetap sebagai histori)")


def record(conn, name: str, target_date: str, prev: dict | None, result, run_id: int | None = None) -> bool:
    """Catat hasil ke master_snapshots setelah upload berhasil. Gagal mencatat -> False (job tetap sukses)."""
    snap = getattr(result, "snapshot", None)
    if not snap or result.s3_key is None:
        return False
    if snap["action"] == "new" or prev is None:
        return save_master_snapshot(conn, name, target_date, result.s3_key, snap["hash"], result.rows,
                                    first_seen=target_date, run_id=run_id)
    first_seen = prev["first_seen"] if snap["hash"] == prev["data_hash"] else target_date
    return save_master_snapshot(conn, name, target_date, result.s3_key, snap["hash"], result.rows,
                                first_seen=first_seen, run_id=run_id, snapshot_id=prev["id"])


def latest(conn, name: str) -> dict | None:
    return latest_master_snapshot(conn, name)
