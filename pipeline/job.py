"""Definisi struktur satu Job. Kamu tidak perlu mengedit file ini."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
import pyarrow as pa


@dataclass
class Job:
    name: str
    query: str                                        # boleh EXEC SP + SELECT, atau SELECT biasa
    output: str
    params: list = field(default_factory=list)        # parameter untuk query (urutan sesuai '?')
    overrides: dict[str, pa.DataType] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    # Kalau diisi: dipanggil dengan dict kolom->nilai dari baris pertama hasil query,
    # harus mengembalikan S3 key tujuan upload. None = job ini tidak diupload ke S3.
    s3_key: Optional[Callable[[dict], str]] = None
    # False = job tidak tergantung tanggal (data master). Dipakai exporter.py untuk
    # memutuskan job ini cukup dijalankan sekali walau diberi range banyak tanggal.
    needs_date: bool = True
    # Data quality (pipeline/dq.py), diisi template bronze. None = pemeriksaan itu dilewati.
    # count_query: hitung baris sumber dengan filter IDENTIK (parameternya = params).
    count_query: Optional[str] = None
    # date_check: kolom tanggal/periode hasil harus di dalam target_date --
    # {"column": c, "min": v, "max_excl": v} (rentang) atau {"column": c, "equals": v}. Boleh DAFTAR spec
    # (bronze_monthly_ym: kolom tahun + kolom bulan, tiap kolom diperiksa sendiri).
    date_check: Optional[dict | list] = None
    # True = snapshot master berbasis perubahan (pipeline/snapshots.py): folder baru hanya kalau data
    # berubah. Diisi build_job() untuk semua job ber-layout S3 master.
    change_snapshot: bool = False
    # Job periodik (bronze mingguan/bulanan): (awal, akhir) INKLUSIF periode yang memuat target_date, ISO.
    # Satu run menarik SELURUH periode, jadi exporter.py / run_store.plan_items menjalankannya sekali per
    # periode (bukan sekali per tanggal) dengan label tanggal = awal periode. None = bukan job periodik.
    period: Optional[tuple[str, str]] = None
