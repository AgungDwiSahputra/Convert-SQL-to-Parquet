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
