"""
PERIODE KALENDER -- minggu & bulan untuk job bronze mingguan/bulanan.

Dipakai job_templates (batas periode query + partisi S3), freshness (kesegaran & celah), dq (volume per
periode), dan s3_inventory (partisi S3 -> tanggal). Modul ini sengaja tidak mengimpor modul pipeline lain,
supaya aman dipakai dari mana pun tanpa impor melingkar.

Minggu = konvensi tabel minggu PZO (T_PZO_Week_Temp; cocok 100% untuk 2020-2028): Senin-Minggu, minggu lintas
bulan ikut bulan hari KAMIS-nya, N = urutan minggu di bulan itu. Bulan = bulan kalender.
"""

from __future__ import annotations

from datetime import date, timedelta

PERIOD_KINDS = ("week", "month")


def pzo_week(d: date) -> tuple[int, int, int, date, date]:
    """
    Minggu kalender konvensi tabel minggu PZO untuk tanggal d.
    Return (tahun, bulan, N, senin, minggu).
    """
    monday = d - timedelta(days=d.weekday())
    thursday = monday + timedelta(days=3)
    first = thursday.replace(day=1)
    first_thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return thursday.year, thursday.month, (thursday - first_thursday).days // 7 + 1, monday, monday + timedelta(days=6)


def pzo_week_start(year: int, month: int, n: int) -> date:
    """Kebalikan pzo_week: Senin minggu ke-N bulan itu. ValueError kalau bulan itu tidak punya minggu ke-N."""
    first = date(year, month, 1)
    monday = first + timedelta(days=(3 - first.weekday()) % 7 + 7 * (n - 1) - 3)
    if n < 1 or pzo_week(monday)[:3] != (year, month, n):
        raise ValueError(f"minggu ke-{n} {year}-{month:02d} tidak ada")
    return monday


def period_bounds(kind: str, d: date) -> tuple[date, date]:
    """Periode yang memuat tanggal d (batas awal & akhir INKLUSIF): 'week' = Senin-Minggu, 'month' = bulan kalender."""
    if kind == "week":
        start = d - timedelta(days=d.weekday())
        return start, start + timedelta(days=6)
    if kind == "month":
        start = d.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    raise ValueError(f"jenis periode tidak dikenal: {kind!r} (pilihan: {', '.join(PERIOD_KINDS)})")
