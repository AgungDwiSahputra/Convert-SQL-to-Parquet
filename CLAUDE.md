# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Baca `README.md` terlebih dahulu

`README.md` di root repo ini ditulis khusus supaya AI bisa langsung paham alur, batasan, dan
aturan modifikasi tanpa membaca ulang seluruh kode — lengkap dengan referensi `file.py:NN`.
**Sebelum melakukan perubahan non-trivial di `pipeline/`, `exporter.py`, atau layout S3, baca
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
- Salin `.env.example` → `.env`, isi kredensial SQL Server + AWS S3 (dibaca `pipeline/config.py` via
  `python-dotenv`; jangan pernah hard-code kredensial di `pipeline/config.py`).
- Salin `jobs.py.example` → `jobs.py`. **`jobs.py` sengaja masuk `.gitignore`** — berisi
  query/nama tabel/struktur S3 key nyata milik environment masing-masing, jadi setiap
  clone/environment punya `jobs.py` sendiri yang tidak ter-commit. `jobs.py.example` adalah
  template dengan helper (`bronze_daily`, `bronze_master`, `sp_job`, `s3_daily`, `s3_weekly`,
  `s3_master`) yang normalnya tidak perlu diubah — hanya isi `build_jobs()`-nya yang diganti
  per environment.
- Jalankan `python migrate_jobs.py` → membuat **`jobs.db`** (SQLite, juga gitignored) dari
  `jobs.py` — **sekali saja per environment** (bootstrap). **`exporter.py` membaca daftar job dari
  `jobs.db`, bukan dari `jobs.py`.** Sejak Fase 2 job ditambah/diubah **lewat Web UI** (tercatat di
  `job_history`); `migrate_jobs.py --replace` ditolak kalau sudah ada edit UI (kecuali
  `--discard-ui-edits`). Migrasi menolak menulis kalau parity check gagal.

Tidak ada test suite otomatis di proyek ini. Verifikasi perubahan lewat run manual, mis.
`python exporter.py --date <tanggal> --skip-s3 --only <nama_job>` lalu cek file di `output/`.
Perubahan di `pipeline/job_templates.py` / `pipeline/job_store.py` / `migrate_jobs.py` wajib lolos
`python migrate_jobs.py --check` (bukti Job identik byte-per-byte dengan `jobs.py`).

## Commands

```powershell
python migrate_jobs.py                                             # bootstrap jobs.db dari jobs.py (env baru)
python migrate_jobs.py --check                                      # bandingkan jobs.db dgn jobs.py (parity)
python exporter.py --list                                          # daftar job (termasuk nonaktif), tanpa eksekusi
python exporter.py                                                  # jalan utk tanggal default (kemarin, WIB)
python exporter.py --date 2026-07-20 --only awl ars                 # tanggal tertentu, job tertentu
python exporter.py --only "bronze_hms_*" --skip-s3                  # wildcard: satu grup job (kutip di shell)
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15    # mode range
python exporter.py --skip-s3 -b 20000 -c snappy                     # ubah batch size & kompresi
```

`run_exporter.bat` adalah wrapper untuk Windows Task Scheduler (cd ke folder sendiri dulu, cari
`python`/`py` lewat `scripts/find_python.bat`) — menjalankan `--only "bronze_*" --skip-local` terjadwal.
**Jangan menjalankan `run_*.bat` asli untuk menguji**: `run_exporter.bat` mengunggah ke S3 produksi dan
`run_worker.bat` memakai `jobs.db` asli (uji lewat salinan di folder lain + skrip python palsu).

Web UI (Fase 1, detail di README.md §8) — dua proses terpisah + build frontend:

```powershell
cd frontend; npm install; npm run build; cd ..          # React+Tailwind -> frontend/dist
python manage.py add-user <username.sso> --role admin    # admin pertama
python -m uvicorn webapp.main:app --port 8000            # = run_web.bat
python worker.py                                         # = run_worker.bat (eksekutor antrian run)
```

## Arsitektur (ringkas — detail lengkap di README.md)

Folder proyek hanya berisi **skrip yang dijalankan langsung** (`exporter.py`, `worker.py`,
`manage.py`, `migrate_jobs.py`, `run_*.bat`); kode inti yang dipakai bersama ada di package
**`pipeline/`** (impor `from pipeline.xxx import ...`), kode khusus web di `webapp/`. Modul library
baru masuk `pipeline/`, bukan folder proyek. Jangan mengimpor nama berawalan `_` dari modul lain.
Semua path default (`output/`, `logs/`, `jobs.db`, `backups/`) relatif ke folder proyek
(`pipeline/config.PROJECT_DIR`) dan bisa diganti lewat `.env` (`OUTPUT_DIR`, `LOG_DIR`, `JOBS_DB`,
`RUN_LOG_DIR`, `BACKUP_DIR`) — dipakai untuk mengisolasi uji dari data asli.

| File | Peran | Kapan diedit |
|---|---|---|
| `jobs.py` | Format lama daftar ekspor (gitignored) — hanya bootstrap env baru & arsip. `from job import Job` di dalamnya tetap jalan (alias di `migrate_jobs.py`) | Tidak lagi |
| `jobs.db` | Daftar ekspor yang **dibaca exporter** (SQLite, gitignored): nama + template + args JSON + enabled + notes + kunci S3 | **Lewat Web UI** (Daftar Job → Tambah/Edit) |
| `exporter.py` | CLI: argumen, loop tanggal × job, ringkasan & exit code | Saat menambah opsi CLI |
| `migrate_jobs.py` | Migrasi `jobs.py` → `jobs.db` via AST + parity check | Saat menambah template (`HELPER_TO_TEMPLATE`) |
| `pipeline/export.py` | Mesin eksekusi (CLI, worker, Web UI): deteksi schema, batching, tulis parquet, upload S3, setup log | Jarang — hanya kalau mekanismenya berubah |
| `pipeline/job_validation.py` | Aturan validasi job dari UI (identifier dicek ke SQL Server, keunikan folder S3, role template, kunci S3) | Saat aturan/template berubah |
| `pipeline/job_templates.py` | Template job (pindahan helper `jobs.py`) + penyusun S3 key. Generik, masuk Git | Saat butuh pola query baru |
| `pipeline/job_store.py` | Akses `jobs.db`, `build_jobs(target_date, rows)`, `default_target_date()` | Saat struktur tabel berubah |
| `pipeline/job.py` | `@dataclass Job` — struktur satu job | Hanya kalau menambah field baru ke `Job` |
| `pipeline/dq.py` | Data quality Bronze: jumlah baris, skema, rentang tanggal — diperiksa di `run_job()` antara tulis & upload | Saat aturan DQ berubah |
| `pipeline/freshness.py` | Kesegaran data + celah (periode di tengah riwayat yang belum ter-load) per job dari `dq_results` + `s3_inventory`, penjaga harian (default nonaktif) | Saat aturan kesegaran/celah berubah |
| `pipeline/s3_inventory.py` | Inventaris S3: tanggal partisi yang benar-benar ada di S3 per job aktif (hanya LIST, harian oleh worker / tombol Pengaturan / `manage.py scan-s3`) | Saat layout partisi S3 berubah |
| `pipeline/config.py` | Connection string, `BATCH_SIZE`, `COMPRESSION`, path, kredensial & setelan Web UI dari `.env` | Saat setelan global berubah |
| `webapp/` | FastAPI: login SSO → session sendiri, API jobs/runs/users, sajikan `frontend/dist` | Fitur Web UI |
| `pipeline/run_store.py` / `worker.py` | Antrian & riwayat run (`runs`/`run_items` di `jobs.db`) / proses eksekutornya (+ thread scheduler, notifikasi, pemeliharaan) | Mekanisme eksekusi run UI |
| `pipeline/scheduler.py` / `notify.py` / `maintenance.py` / `app_settings.py` | Jadwal run otomatis (WIB) / email run gagal & anomali 0 baris / backup `jobs.db` & bersih log harian / setelan non-rahasia (tabel `settings`) | Fase 3 — README §10 |
| `frontend/` | React 19 + Tailwind 4 + React Router 7 (Vite). Warna lewat token `brand-*`/`gray-*`/`blue-*` di `src/index.css` (palet hijau, mode terang) — jangan pakai warna Tailwind lain (indigo/slate/emerald) atau kelas `dark:` | Tampilan Web UI |

Hal-hal penting yang tidak boleh diubah sembarangan (detail & alasan lengkap di README.md §5):
- **Nama file S3 konstan antar-run** (bronze = nama file output job, mis. `bronze_xxx.parquet`;
  `sp_job`/`custom_sql` default `part-00001.parquet`) — tiap job menulis data penuh per-partisi
  (bukan delta), jadi nama auto-increment/timestamp bikin data terhitung dobel di Athena/Spark.
- **Bentuk path S3**: `{layer}/{module}/{dataset}/<partisi>/{file}` — `module`/`dataset` folder polos
  (BUKAN `source=`/`dataset=`), `module` opsional, nama segmen case-sensitive. Path produksi terkunci;
  jangan ubah bentuknya (README §5).
- **Partisi Hive-style `kunci=nilai`** (`recorded_year=`, dst.) diambil dari `target_date`
  proses, bukan jam run — supaya re-run/backfill idempoten (overwrite partisi yang sama).
- **Kolom audit `_ingested_at`** ditambahkan otomatis ke semua job (bronze & gold) di
  `pipeline/export.py`, terpisah dari partisi tanggal bisnis — jangan disamakan maknanya.
- Placeholder parameter query pakai `?` (gaya qmark pyodbc), bukan `@param`.
- `name`, `output`, dan hasil `s3_key` harus unik antar job — tabrakan bersifat senyap (saling
  menimpa file, tanpa error).
- **`pipeline/job_templates.py` masuk Git: jangan tulis nilai spesifik environment** (nama DB/SP, IP linked
  server, nama tabel) di sana — jadikan argumen template; nilainya tersimpan per job di `jobs.db`.
  Whitespace string query di template sengaja identik dengan helper lama (parity byte-per-byte).
- Skema `jobs.db` berubah = TAMBAH elemen di `job_store._MIGRATIONS` (jangan ubah yang lama).
  Koneksi web/worker lewat `run_store.open_db()` (autocommit, `check_same_thread=False` karena
  FastAPI memakai threadpool; transaksi multi-statement lewat `_tx()` = `BEGIN IMMEDIATE`).
- Di Windows `mimetypes` membaca registry (`.js` bisa jadi `text/plain` → halaman putih);
  `webapp/main.py` mengunci MIME `.js/.css/.svg` secara eksplisit — jangan dihapus.
- **Path S3 terkunci** (`jobs.s3_locked_at`, diisi saat upload pertama oleh CLI & worker) hanya boleh
  dipindah admin + `confirm_s3_move`. Template baru = tambah juga di `job_validation.TEMPLATE_SPECS`
  (role minimal) dan `TEMPLATE_FIELDS` di `frontend/src/lib/jobs.js`.
- Parameter string pyodbc dikirim sebagai NVARCHAR → filter kolom VARCHAR tanpa `CAST(? AS VARCHAR(n))`
  memindai seluruh tabel (terukur 70 dtk vs 0,2 dtk di tabel 64 jt baris). Jangan dihapus di template.
- Metadata SQL Server: JANGAN join `sys.partitions` untuk semua tabel (40-60+ dtk di database besar);
  hitung baris per tabel terpilih saja (`webapp/catalog.table_rows`).
- **Kredensial (SMTP, SSO, SQL Server, AWS) hanya di `.env` server** — jangan pernah ditulis ke kode,
  dokumen, database (`settings` hanya untuk nilai non-rahasia), atau ditampilkan di UI/API.
- Ekspor ad-hoc Viewer **hanya dari job terdaftar** (keputusan keamanan: login exporter bisa membaca
  tabel sensitif); tabel bebas khusus Operator/Admin. Jangan dilonggarkan tanpa persetujuan pemilik.
  Akun SSO yang daftar otomatis (saklar `auth.auto_viewer`) = Viewer dengan `users.can_export = 0`: Ekspor
  Cepat baru setelah admin menyetujui (`webapp.deps.can_quick_export`). Akun nonaktif selalu ditolak login.
- Saat menguji worker/jadwal, opsi run WAJIB `skip_s3: true` — default opsi run = upload ke S3 produksi.
- **Snapshot master** (README §5, `pipeline/snapshots.py`): job ber-layout master (ditandai
  `Job.change_snapshot` oleh `build_job`) hanya membuat folder baru kalau hash data berubah; data sama =
  upload ke tanggal baru lalu HAPUS objek lama (satu-satunya tempat pipeline menghapus objek S3 — hanya key
  yang tercatat di `master_snapshots`). Backfill tanggal lebih lama dari snapshot terakhir tidak diupload.
- **Data quality** (README §6): pemeriksaan berat yang gagal (`dq.DQBlocked`) MENAHAN upload. String query
  template tidak diubah demi parity — `count_query` diturunkan otomatis (`count_query_for()`). Skema acuan di
  `jobs.schema_json`, diperbarui hanya setelah upload S3 sukses; lepas dengan `manage.py accept-schema <job>`.
  CLI & worker memanggil `dq.prepare()` sebelum dan `dq.after_job()` sesudah `run_job()` — hasil masuk
  `dq_results` (baseline volume, juga untuk run CLI). Irama job (`jobs.cadence`) menentukan pembanding
  volume; anomali 0 baris hanya untuk irama harian. Pesan log/DQ ASCII saja (konsol cp1252).
- Jadwal (skema v8): frekuensi harian/mingguan/bulanan + rentang (`window_mode`: offset / last_days /
  prev_week / prev_month) + cakupan per irama. Rentang > 1 hari: master tetap sekali berlabel tanggal awal.
  Tabel `schedules` punya CHECK — mengubah nilai yang diizinkan = bangun ulang tabel (lihat migrasi v8).
- Label minggu di peringatan celah = konvensi tabel minggu PZO (`freshness.pzo_week`: Senin–Minggu, ikut bulan hari
  Kamis). Tabel sumber `T_PZO_Week_Temp` punya beberapa baris 2029–2030 yang salah ketik (mis. Year 1030).
- Inventaris S3 hanya MEMBACA (ListObjectsV2). Pesan error S3 ke UI/settings = pesan sendiri per kode error,
  jangan pesan asli AWS (memuat ARN user IAM, nomor akun, nama bucket). Hari 0 baris tidak membuat file di S3,
  jadi "tidak ada di S3" tidak sama dengan "belum ter-load" — celah ditutup oleh hasil 0 baris di `dq_results`.
- `migrate_jobs.py --check` menjalankan `init_db` (migrasi skema) pada `jobs.db` yang dipakainya — saat ada
  migrasi baru yang belum dirilis, jalankan dengan `JOBS_DB` menunjuk ke salinan.
- **Deploy Docker = Windows container** (`Dockerfile.windows`, README §11): base `servercore:ltsc2016`
  WAJIB cocok dengan host server Docker (Windows Server 2016, process isolation) — jangan ganti ke image Linux. Di dalam `RUN`
  jangan pakai kutip ganda (dibuang parsing argumen Windows sebelum PowerShell membacanya). IP untuk
  `HOSTS_ENTRIES` hanya lewat `--build-arg`, tidak ditulis ke file. Versi di `requirements.txt` dikunci.
