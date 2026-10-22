# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Baca `README.md` terlebih dahulu

`README.md` di root repo ini ditulis khusus supaya AI bisa langsung paham alur, batasan, dan
aturan modifikasi tanpa membaca ulang seluruh kode — lengkap dengan referensi `file.py:NN`.
**Sebelum melakukan perubahan non-trivial di `exporter.py`, `jobs.py`, atau layout S3, baca
README.md secara utuh.** Ringkasan di bawah ini hanya menyorot yang paling penting.

## Apa proyek ini

Pipeline satu-arah **SQL Server 2012 → Parquet → AWS S3**, dibuat karena SQL Server 2012 tidak
punya dukungan Parquet native. Data ditarik streaming (`pyodbc fetchmany`, bukan load penuh ke
memori) lalu ditulis batch-per-batch dengan `pyarrow.parquet.ParquetWriter`. Konteks bisnis:
data monitoring air perkebunan (AWL, ARS, curah hujan manual, TMAT/TMAS) plus tabel master —
tujuan akhirnya dataset untuk AI/ML, karena itu presisi angka dijaga (`decimal`, bukan `float`).

## Setup

```powershell
pip install -r requirements.txt
```

- Butuh **ODBC Driver 17 for SQL Server** terinstall terpisah di OS (bukan lewat pip).
- Salin `.env.example` → `.env`, isi kredensial SQL Server + AWS S3 (dibaca `config.py` via
  `python-dotenv`; jangan pernah hard-code kredensial di `config.py`).
- Salin `jobs.py.example` → `jobs.py`. **`jobs.py` sengaja masuk `.gitignore`** — berisi
  query/nama tabel/struktur S3 key nyata milik environment masing-masing, jadi setiap
  clone/environment punya `jobs.py` sendiri yang tidak ter-commit. `jobs.py.example` adalah
  template dengan helper (`bronze_daily`, `bronze_master`, `sp_job`, `s3_daily`, `s3_weekly`,
  `s3_master`) yang normalnya tidak perlu diubah — hanya isi `build_jobs()`-nya yang diganti
  per environment.

Tidak ada test suite otomatis di proyek ini. Verifikasi perubahan lewat run manual, mis.
`python exporter.py --date <tanggal> --skip-s3 --only <nama_job>` lalu cek file di `output/`.

## Commands

```powershell
python exporter.py --list                                          # daftar job, tanpa eksekusi
python exporter.py                                                  # jalan utk TARGET_DATE default (jobs.py)
python exporter.py --date 2026-07-20 --only awl ars                 # tanggal tertentu, job tertentu
python exporter.py --only "bronze_hms_*" --skip-s3                  # wildcard: satu grup job (kutip di shell)
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15    # mode range
python exporter.py --skip-s3 -b 20000 -c snappy                     # ubah batch size & kompresi
```

`run_exporter.bat` adalah wrapper untuk Windows Task Scheduler (auto-detect `python`/`py` di
PATH, cd ke folder sendiri dulu) — menjalankan `--only "bronze_*" --skip-local` terjadwal.

## Arsitektur (ringkas — detail lengkap di README.md)

| File | Peran | Kapan diedit |
|---|---|---|
| `jobs.py` | Daftar ekspor (gitignored, lihat di atas) | **Satu-satunya file yang normalnya perlu diedit** untuk menambah/mengubah ekspor |
| `exporter.py` | Mesin eksekusi: CLI, koneksi, deteksi schema, batching, tulis parquet, upload S3 | Jarang — hanya kalau mekanismenya berubah |
| `job.py` | `@dataclass Job` — struktur satu job | Hanya kalau menambah field baru ke `Job` |
| `config.py` | Connection string, `BATCH_SIZE`, `COMPRESSION`, kredensial dari `.env` | Saat setelan global berubah |

Hal-hal penting yang tidak boleh diubah sembarangan (detail & alasan lengkap di README.md §5):
- **Nama file S3 konstan** (`part-00001.parquet`) — tiap job menulis data penuh per-partisi
  (bukan delta), jadi nama auto-increment/timestamp bikin data terhitung dobel di Athena/Spark.
- **Partisi Hive-style `kunci=nilai`** (`recorded_year=`, dst.) diambil dari `target_date`
  proses, bukan jam run — supaya re-run/backfill idempoten (overwrite partisi yang sama).
- **Kolom audit `_ingested_at`** ditambahkan otomatis ke semua job (bronze & gold) di
  `exporter.py`, terpisah dari partisi tanggal bisnis — jangan disamakan maknanya.
- Placeholder parameter query pakai `?` (gaya qmark pyodbc), bukan `@param`.
- `name`, `output`, dan hasil `s3_key` harus unik antar job — tabrakan bersifat senyap (saling
  menimpa file, tanpa error).
