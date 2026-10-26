# SQL Server → Parquet → S3

Pipeline satu arah untuk mengekspor data SQL Server 2012 ke file Parquet dan mengunggahnya ke AWS S3,
lengkap dengan Web UI untuk mengelola dan menjalankan ekspor. Dokumen ini ditulis supaya manusia
maupun AI bisa paham alur, batasan, dan aturan modifikasi tanpa membaca seluruh kode. Referensi kode
ditulis `file.py:fungsi()`, bukan nomor baris.

## 1. Gambaran

```
SQL Server 2012 ──pyodbc fetchmany──▶ pipeline/export.py ──pyarrow ParquetWriter──▶ S3 (dan/atau output/ lokal)
 (SELECT / EXEC SP)                    streaming per batch
```

- **Kenapa ada**: SQL Server 2012 tidak bisa menulis Parquet. Data ditarik **streaming** per batch,
  jadi tabel yang jauh lebih besar dari RAM tetap bisa diekspor.
- **Untuk apa**: dataset AI/ML dari data monitoring air perkebunan (AWL, ARS, curah hujan, TMAT/TMAS)
  plus tabel master (estate, block, device IoT). Karena itu presisi angka dijaga (`decimal`, bukan `float`).
- **Daftar ekspor** ("job") tersimpan di `jobs.db` (SQLite) dan dikelola lewat Web UI.
- **Dua cara menjalankan**: CLI `exporter.py` (mis. dari Task Scheduler), atau Web UI → antrian run →
  `worker.py` (termasuk jadwal otomatis di dalam aplikasi).

## 2. Struktur proyek

```
LoadParquetProcess/
├── exporter.py, worker.py, manage.py, migrate_jobs.py   skrip yang dijalankan langsung
├── run_exporter.bat, run_web.bat, run_worker.bat         wrapper Windows (cd ke folder sendiri)
├── pipeline/            kode inti (library, tidak dijalankan langsung)
├── webapp/              API FastAPI + penyaji frontend
├── frontend/            React + Tailwind (build → frontend/dist)
├── scripts/             find_python.bat, check_runtime.py, check_network.py
├── Dockerfile.windows, .dockerignore                     image Windows container (§11)
├── jobs.db, .env, jobs.py      milik environment, gitignored
└── output/, logs/, backups/    hasil run, gitignored
```

| File | Isi | Kapan diedit |
|---|---|---|
| `exporter.py` | CLI: argumen, loop tanggal × job, exit code | Menambah opsi CLI |
| `worker.py` | Eksekutor antrian run + thread jadwal, notifikasi, pemeliharaan | Mekanisme eksekusi run |
| `manage.py` | Kelola user Web UI dari command line | — |
| `migrate_jobs.py` | `jobs.py` → `jobs.db` + parity check (§4) | Menambah template pengganti helper `jobs.py` (`HELPER_TO_TEMPLATE`) |
| `pipeline/export.py` | Mesin ekspor: deteksi schema, batching, tulis Parquet, upload S3, setup log | Jarang |
| `pipeline/dq.py` | Pemeriksaan data quality per dataset sebelum upload (§6) | Aturan DQ berubah |
| `pipeline/job_templates.py` | Template job + penyusun S3 key (`s3_daily`/`s3_weekly`/`s3_master`) | Pola query baru |
| `pipeline/job_validation.py` | Aturan validasi job dari Web UI | Aturan / template baru |
| `pipeline/job_store.py` | Akses `jobs.db`: skema (`_MIGRATIONS`), `build_jobs()`, `default_target_date()` | Struktur tabel |
| `pipeline/job.py` | `@dataclass Job` | Field baru |
| `pipeline/config.py` | Koneksi, batch, kompresi, path, setelan — semua dari `.env` | Setelan global |
| `pipeline/run_store.py`, `scheduler.py`, `notify.py`, `maintenance.py`, `app_settings.py` | Antrian & riwayat run, jadwal, email, backup & bersih log, setelan non-rahasia | Fitur operasional |
| `webapp/` | `main.py` (app, SPA, kunci MIME), `routes_*.py` per kelompok API, `catalog.py` (metadata SQL Server, uji coba, ekspor cepat) | Fitur Web UI |
| `frontend/` | React 19 + Tailwind 4 + React Router 7 (Vite) | Tampilan |

Path default (`output/`, `logs/`, `jobs.db`, `backups/`) relatif ke folder proyek dan bisa diganti di
`.env`: `OUTPUT_DIR`, `LOG_DIR`, `JOBS_DB`, `RUN_LOG_DIR`, `BACKUP_DIR`.

## 3. Mulai cepat

Prasyarat: Python 3.12, **ODBC Driver 17 for SQL Server** (dipasang di OS, bukan lewat pip), Node.js.

```powershell
pip install -r requirements.txt                        # versi dikunci
Copy-Item .env.example .env                            # isi kredensial SQL Server, AWS, SSO, SMTP
python migrate_jobs.py                                 # environment baru: buat jobs.db dari jobs.py (sekali)
cd frontend; npm install; npm run build; cd ..         # build Web UI
python manage.py add-user <username.sso> --role admin  # admin pertama
run_web.bat                                            # Web UI di :8000 (WEB_HOST / WEB_PORT bisa diubah)
run_worker.bat                                         # eksekutor run + jadwal — SATU instance per server
```

Environment yang sudah punya data: pindahkan `jobs.db` lama (cara aman di §11) alih-alih `migrate_jobs.py`.

```powershell
python exporter.py --list                                              # daftar job, termasuk nonaktif
python exporter.py                                                     # job aktif, tanggal default = kemarin (WIB)
python exporter.py --date 2026-07-20 --only "bronze_hms_*" --skip-s3   # satu grup job, simpan lokal saja
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15       # rentang tanggal
```

Tidak ada test suite otomatis. Verifikasi perubahan dengan `python exporter.py --date <tgl> --skip-s3
--only <job>` lalu cek `output/`. Perubahan di template / penyimpanan job wajib lolos
`python migrate_jobs.py --check`.

## 4. Daftar job (`jobs.db`)

Satu baris tabel `jobs` = satu dataset: nama + **template** + argumen JSON + aktif/nonaktif + catatan.
`jobs.db` adalah sumber kebenaran dan diubah lewat Web UI (§9); setiap perubahan tercatat di
`job_history`. Semua nilai yang menentukan query & path S3 (database, tabel, layer, module, dataset,
nama file, nama SP, linked server) disimpan **eksplisit per job**, jadi mengubah default di template
tidak diam-diam memindahkan job lama. Satu-satunya setelan global yang ikut menentukan path S3:
`S3_PREFIX`.

| Template | Untuk | Dibuat oleh |
|---|---|---|
| `bronze_daily` | Harian, kolom tanggal datetime/date | Operator, Admin |
| `bronze_daily_strdate` / `bronze_daily_strdate_eq` | Harian, kolom tanggal teks (`YYYY-MM-DD` rentang / format lain kesetaraan) | Operator, Admin |
| `bronze_daily_epoch_ms` | Harian, kolom tanggal epoch milidetik | Operator, Admin |
| `bronze_daily_join` | Harian, filter tanggal lewat JOIN tabel induk | Operator, Admin |
| `bronze_master` | Master, snapshot penuh tiap run | Operator, Admin |
| `bronze_arcsde_view` | View ArcSDE lewat linked server | Admin |
| `sp_job` | SP dispatcher — menjalankan ETL Load (**menulis** ke SQL Server) | Admin |
| `custom_sql` | SQL bebas; nilai tanggal lewat `{"$var": "target_date"}` / `{"$var": "next_date"}` | Admin |

Field `Job` (`pipeline/job.py`):
- `name` — unik, dipakai `--only` dan log. `output` — nama file `.parquet` lokal.
- `query` — SELECT, atau `EXEC` SP yang diakhiri SELECT; placeholder `?`. `params` — nilai tiap `?`, berurutan.
- `overrides` — paksa tipe Arrow per kolom; disimpan sebagai teks (mis. `"decimal128(18, 4)"`) dan
  dibaca lewat whitelist `parse_arrow_type()`, bukan `eval`.
- `labels` — kolom target ML (hanya dicatat ke log). `s3_key` — fungsi penghasil key, `None` = tidak
  diupload. `needs_date` — `False` untuk data master.

**`jobs.py`** (format lama, gitignored) kini hanya untuk bootstrap environment baru:

```powershell
python migrate_jobs.py --dry-run   # analisis + parity, tanpa menulis
python migrate_jobs.py             # isi jobs.db pertama kali
python migrate_jobs.py --check     # bandingkan jobs.db dengan jobs.py (beda = wajar setelah edit UI)
python migrate_jobs.py --replace   # sinkron ulang — DITOLAK kalau sudah ada edit UI (kecuali --discard-ui-edits)
```

Migrasi membedah `jobs.py` lewat AST (entri ter-comment → `enabled=0`, komentar di atas entri →
`notes`), lalu menjalankan **parity check**: Job dari database harus identik byte-per-byte dengan
`build_jobs()` asli (query, params, output, overrides, labels, needs_date, S3 key, urutan) untuk
beberapa tanggal uji, termasuk kabisat dan pergantian tahun. Ada beda → database tidak ditulis (exit 2).

## 5. Layout S3

```
[prefix/]{layer}/{module}/{dataset}/recorded_year=YYYY/recorded_month=MM/recorded_day=DD/{file}      harian
[prefix/]{layer}/{module}/{dataset}/recorded_year=YYYY/recorded_month=MM/recorded_week=W{n}/{file}   mingguan
[prefix/]{layer}/{module}/master_{dataset}/snapshot_date=YYYY-MM-DD/{file}                           master
```

Contoh: `bronze/ars/hk_transactions/recorded_year=2026/recorded_month=07/recorded_day=20/bronze_ars_hk_transactions.parquet`

- `prefix` = `S3_PREFIX` (default kosong), bucket = `S3_BUCKET_NAME`. Path lengkap tiap job tampil di Web UI.
- `module` dan `dataset` folder polos; hanya partisi tanggal yang `kunci=nilai`. `module` opsional
  (kosong → segmen dilewati). Nama segmen case-sensitive: `pzo/` ≠ `PZO/`. Job baru memakai huruf
  kecil; beberapa job gold lama memakai huruf besar dan dibiarkan karena path-nya sudah terkunci.
- Nama file: template bronze = nama file output job; `sp_job`/`custom_sql` default `part-00001.parquet`;
  `bronze_master` bisa memakai `s3_file_name` untuk job lama yang terlanjur memakai nama generik.
- `s3_weekly` membaca `week_year`/`week_month`/`week_of_month` dari **baris pertama** hasil query
  (batas minggu ditentukan SQL Server, jangan dihitung ulang di Python). Itu sebabnya `s3_key` berupa
  fungsi yang menerima `first_row`.
- Rancangan lama `source=`/`dataset=` tidak pernah diterapkan. Jangan "diperbaiki" di kode — semua
  path produksi sudah terkunci, dan mengubah bentuk path berarti memindahkan seluruh data di S3.

**Keputusan desain — jangan diubah tanpa alasan kuat:**

1. **Partisi berbentuk `kunci=nilai`** → Athena/Glue mengenali kolom partisi dan bisa partition pruning.
2. **Nilai partisi dari `target_date`, bukan jam run** → re-run/backfill menimpa partisi yang sama (idempoten).
3. **Nama file konstan antar-run** → tiap job menulis data penuh per partisi (bukan delta), jadi nama
   yang berubah-ubah membuat data terhitung dobel. Dua job juga tidak boleh berbagi folder dataset
   walau nama filenya beda.
4. **Master diberi awalan `master_`** → dataset transaksional dan master yang bernama sama tidak jatuh
   ke folder yang sama dengan skema partisi berbeda (Glue gagal menyimpulkan skema).
5. **`module` selalu tepat di bawah `layer`** → satu sistem mudah ditelusuri lintas layer. Kalau modul
   belum jelas, kosongkan daripada menebak — path terkunci setelah upload pertama.

Master disimpan sebagai **snapshot per tanggal** (bukan satu file yang ditimpa) supaya dataset ML bisa
direproduksi dan terlihat kapan sebuah entitas muncul atau hilang; dibaca dengan pola "ambil
`MAX(snapshot_date)`".

## 6. Cara ekspor berjalan

**CLI (`exporter.py:main()`)**
- Daftar job dibaca **sekali** di awal; semua tanggal di mode range memakai snapshot yang sama. Hanya
  job aktif yang dijalankan — `--only` pun tidak bisa memilih job nonaktif.
- Tanggal default = **kemarin menurut kalender WIB** (`Asia/Jakarta`, bukan timezone OS).
- `--only` menerima nama persis atau wildcard (`*`, `?`). Pola yang tidak cocok hanya diperingatkan;
  error kalau tidak ada satu pun yang cocok.
- Satu koneksi SQL Server untuk seluruh run. Job yang gagal dicatat lalu lanjut ke job berikutnya,
  kecuali `--stop-on-error`.
- Kredensial S3 di `.env` belum lengkap → upload dilewati dengan peringatan, bukan error.
- Mode range: job harian dijalankan sekali per tanggal (file lokal diberi sufiks `_YYYYMMDD`), job
  master sekali saja.
- Exit code: `0` sukses, `2` ada job gagal, `1` error argumen/koneksi/database. Log ke console dan
  `logs/exporter_*.log`.

**Per job (`pipeline/export.py:run_job()`)**
1. Query dijalankan dengan `params` terpisah (aman dari SQL injection).
2. Resultset tanpa kolom (dari `EXEC sp_ETL_Load…` sebelum SELECT akhir) dilompati
   (`advance_to_resultset_with_columns()`). Tidak ada resultset berkolom → tidak ada file.
3. Schema Arrow dibangun dari metadata kolom (tabel di bawah), ditambah kolom `_ingested_at`.
4. `fetchmany(batch)` → satu **row group** per batch → `ParquetWriter`.
5. **Data quality** diperiksa (lihat di bawah); gagal berat → upload ditahan.
6. Upload S3 hanya kalau ada baris dan S3 aktif. `--skip-local` membentuk Parquet di memori lalu
   langsung upload, tetapi job **tanpa** tujuan S3 tetap disimpan lokal supaya hasilnya tidak hilang.

**Tipe kolom** dibaca dari `cursor.description` (`arrow_type_from_description()`), bukan ditebak dari nilai:

| SQL Server | Arrow |
|---|---|
| `decimal` / `numeric` | `decimal128(p, s)` — presisi terjaga; presisi kosong → 38 |
| `bit` / `int` / `float` | `bool_` / `int64` / `float64` |
| `datetime` / `datetime2` | `timestamp("us")` tanpa timezone (diasumsikan WIB) |
| `date` / `time` | `date32` / `time64("us")` |
| `varbinary` | `binary` |
| lainnya (`varchar`, `uniqueidentifier`, …) | `string` |

Override per kolom lewat `overrides`; key = nama kolom persis seperti hasil query.

**Kolom `_ingested_at`** (`timestamp("us", tz="UTC")`) ditambahkan di posisi terakhir semua file: jam
UTC saat job dijalankan, satu nilai untuk seluruh file. Maknanya beda dari partisi tanggal: partisi =
tanggal bisnis (`target_date`), `_ingested_at` = kapan file ditulis. Karena partisi ditimpa, nilainya
hanya mencerminkan load terakhir yang sukses. Riwayat eksekusi ada di Riwayat Run Web UI dan log CLI.

**Data quality** (`pipeline/dq.py`) — diperiksa per dataset **setelah Parquet ditulis, sebelum upload**
(pola *write–audit–publish*), sama untuk CLI, worker, dan jadwal:

| Pemeriksaan | Ditahan (upload tidak jalan) | Peringatan saja |
|---|---|---|
| **Jumlah baris**: `COUNT_BIG(*)` sumber vs baris tertulis. Query hitung = query ekspor dibungkus subquery (`count_query_for()`), dijalankan tepat sebelum ekstraksi di koneksi yang sama | Beda pada job harian untuk tanggal ≤ kemarin WIB (periode tutup) | Beda pada master / tanggal yang masih berjalan; gagal menghitung |
| **Skema** vs acuan (`jobs.schema_json` = skema terakhir yang dipublikasikan) | Kolom hilang, tipe berubah | Kolom baru |
| **Rentang tanggal**: min/max kolom tanggal hasil vs `target_date` | Di luar tanggal proses (filter/format salah) | Kolom tidak ada / kosong semua |

- Cakupan: hitung baris untuk template bronze kecuali ArcSDE; rentang tanggal untuk template harian
  kecuali `bronze_daily_join` (kolom tanggalnya di tabel induk); `sp_job`/`custom_sql` hanya skema.
- **Ditahan** = item gagal dengan pesan `Ditahan DQ: …`; partisi lama di S3 tetap utuh, file lokal (kalau
  ada) ditinggal untuk diperiksa. Email run gagal, "Ulangi yang belum berhasil", dan exit code 2 CLI berlaku.
- Skema acuan diperbarui **hanya setelah upload S3 sukses** (run `--skip-s3` tidak menggesernya) dan
  dilepas otomatis saat template/args job diubah. Perubahan skema sumber yang disengaja:
  `python manage.py accept-schema <job>`, lalu jalankan ulang — run berikutnya menjadi acuan baru.
- Hasil tampil di kolom **DQ** halaman Detail Run (`run_items.dq_status`, `dq_detail`, `source_rows`) dan
  di log (`DQ OK` / `DQ WARNING` / `DQ BLOCKED`).
- Biaya: satu query hitung tambahan per job — query yang sudah lambat jadi kira-kira dua kali lebih lama.

**Batch size** (`-b`, default `100_000`) menentukan puncak memori (batch × lebar baris) sekaligus
ukuran row group. Turunkan kalau memori terbatas; jangan terlalu kecil untuk data besar karena banyak
row group kecil memperlambat Athena/Spark.

| Flag CLI | Efek |
|---|---|
| `--date` / `--start-date` + `--end-date` | Satu tanggal / rentang inklusif |
| `--only NAMA…` | Nama persis atau wildcard |
| `-b N` / `-c zstd\|snappy\|gzip\|none` | Batch size / kompresi (default `zstd`) |
| `--list` | Cetak daftar job lalu keluar |
| `--stop-on-error` | Berhenti di kegagalan pertama |
| `--skip-s3` / `--skip-local` | Simpan lokal saja / upload langsung dari memori |

`run_exporter.bat` (wrapper Task Scheduler) menjalankan `--only "bronze_*" --skip-local`.

## 7. Aturan saat memodifikasi

1. **Tambah/ubah ekspor lewat Web UI**, bukan `jobs.py`. Pola query baru = template di
   `pipeline/job_templates.py` + `TEMPLATE_SPECS` (role minimal) dan aturannya di `job_validation.py` +
   `TEMPLATE_FIELDS` di `frontend/src/lib/jobs.js`.
2. **Placeholder parameter `?`** (gaya qmark pyodbc), bukan `@param`; `params` berurutan sesuai `?`.
3. **`SET NOCOUNT ON;` di awal query SP**, supaya pesan "N rows affected" tidak menjadi resultset pengganggu.
4. **`name`, `output`, hasil `s3_key`, dan folder dataset S3 unik antar job** — tabrakan bersifat
   senyap (saling menimpa tanpa error).
5. **Nama file S3 jangan dinamis** (timestamp, UUID, nomor berurut) — §5 keputusan 3.
6. **Kredensial hanya di `.env`** — jangan di kode, dokumen, database (`settings` hanya untuk nilai
   non-rahasia), atau ditampilkan di UI/API.
7. **`pipeline/job_templates.py` masuk Git**: jangan tulis nilai spesifik environment (nama DB/SP, IP,
   nama tabel) — jadikan argumen template. Whitespace query sengaja identik dengan helper lama (parity).
8. **Field baru di `Job` wajib punya default** supaya job lama tidak rusak.
9. **Skema `jobs.db` berubah = TAMBAH elemen di `job_store._MIGRATIONS`**, jangan ubah yang lama.
   Pesan log dan pesan DQ memakai ASCII (mis. `->`, `!=`) — konsol Windows ber-encoding cp1252.
10. **Modul library baru masuk `pipeline/`** (atau `webapp/` untuk kode khusus web). Jangan mengimpor
    nama berawalan `_` dari modul lain — jadikan publik kalau dipakai bersama.
11. **Jangan hapus** `CAST(? AS VARCHAR(n))` di template (§9) dan penguncian MIME `.js/.css/.svg` di
    `webapp/main.py` (registry Windows bisa memetakan `.js` ke `text/plain` → halaman putih).
12. **Saat menguji**: opsi run `skip_s3: true` (default run = upload ke S3 produksi), pakai salinan
    `jobs.db` dengan `RUN_LOG_DIR` sendiri, dan jangan menjalankan `run_*.bat` asli.

## 8. Web UI

```
Browser (React) ──▶ webapp/ (FastAPI) ──▶ jobs.db (SQLite WAL) ◀── worker.py ──▶ SQL Server / S3
                        │ login                    ▲ runs / run_items
                   portal SSO               logs/runs/run_<id>.log ──▶ log live di UI
```

- **Login** lewat portal SSO → session aplikasi sendiri (cookie HttpOnly, token disimpan sebagai
  hash). Hanya username yang terdaftar & aktif di tabel `users` yang bisa masuk; dari profil SSO hanya
  nama & email yang disimpan. Batas 5× gagal / 10 menit per (IP, username).
- **Role**: `viewer` (lihat; Ekspor Cepat dari job terdaftar), `operator` (+ jalankan/batalkan/ulangi
  run, kelola job bronze & jadwal, ekspor tabel bebas), `admin` (+ template khusus admin, user, pengaturan).
- **Run** dibuat dari halaman Jalankan atau dari jadwal. Semantiknya sama dengan CLI: job harian sekali
  per tanggal, job master sekali dengan snapshot tanggal awal. Default opsi sama dengan
  `run_exporter.bat`: upload S3 ya, simpan lokal tidak.
  - `run_items` menyimpan snapshot definisi job saat run dibuat (audit "dijalankan dengan definisi apa").
  - Beda kecil dengan CLI: job master yang gagal tidak dicoba ulang di tanggal berikutnya — pakai
    "Ulangi yang belum berhasil" (memakai definisi job terbaru).
  - Cancel saat berjalan: job yang sedang jalan diselesaikan, sisanya dibatalkan.
  - Dua run yang memakai job yang sama tidak pernah berjalan bersamaan (klaim run pakai `BEGIN IMMEDIATE`).
  - Worker mengirim heartbeat tiap 15 detik; run yang heartbeat-nya basi > 90 detik → `interrupted`.
- **Skema `jobs.db`**: `jobs`, `job_history`, `users`, `sessions`, `runs`, `run_items`, `workers`,
  `schedules`, `settings`, `notifications`, `adhoc_exports`. Dinaikkan otomatis saat web, worker, atau
  CLI start.
- **Pengembangan frontend**: `python -m uvicorn webapp.main:app --reload` + `cd frontend; npm run dev`
  (Vite :5173 mem-proxy `/api` ke :8000). Warna hanya lewat token `brand-*`/`gray-*`/`blue-*` di
  `frontend/src/index.css` (palet hijau, mode terang). Dokumentasi API: `/api/docs`.

## 9. Kelola job lewat Web UI

**Wizard tambah job** (`frontend/src/pages/JobFormPage.jsx`):
1. Pilih database & tabel dari metadata SQL Server (cache 2 menit).
2. Untuk data harian, template **dipilih otomatis** dari tipe & isi kolom tanggal
   (`webapp/catalog.detect_date_format`, minimal 75% contoh cocok). Format tak dikenal → tidak bisa disimpan.
3. Modul, dataset, nama, dan file disarankan dari nama tabel; path S3 tampil live.
4. **Uji coba**: `SET ROWCOUNT n`, transaksi selalu di-rollback, timeout 90 detik, maks 2 bersamaan.
   Tidak menulis file maupun upload. Override tipe kolom bisa dipilih dari hasil schema.
5. Disimpan **nonaktif**; "Aktifkan sekarang" hanya bisa kalau uji coba berhasil untuk isian yang sama.
   Job aktif bernama `bronze_*` otomatis ikut `run_exporter.bat`.

**Aturan yang dijaga sistem** (`job_validation.validate_job()`, dipanggil live & saat simpan):
- Identifier (db/tabel/kolom/alias) harus identifier SQL biasa, bukan kata kunci T-SQL, dan benar-benar
  ada di SQL Server. Template merangkai query dengan f-string, jadi inilah pencegah SQL injection.
- Nama job, file output, dan folder dataset S3 unik antar job. Nama job tidak bisa diubah setelah dibuat.
- **Kunci path S3** (`jobs.s3_locked_at`) terisi saat upload pertama berhasil (CLI & worker); job hasil
  impor `jobs.py` langsung terkunci. Path terkunci hanya bisa dipindah Admin dengan konfirmasi
  (`confirm_s3_move`); data lama tertinggal di path lama.

**Catatan performa**: pyodbc mengirim string sebagai NVARCHAR. Membandingkannya dengan kolom VARCHAR
membuat SQL Server memindai seluruh tabel (terukur 70 detik vs 0,2 detik di tabel 64 juta baris).
Karena itu `bronze_daily_strdate_eq` memakai `CAST(? AS VARCHAR(n))`. Template lama
`bronze_daily_strdate` sengaja tidak diubah demi parity — untuk tabel teks format lain pakai wizard.

## 10. Operasional

**Jadwal** (`pipeline/scheduler.py`, halaman Jadwal): jam WIB harian/mingguan + cakupan job (pola
nama, modul, atau daftar job) + opsi run. Thread di worker (tiap 20 detik) mengubah jadwal yang jatuh
tempo menjadi run biasa atas nama `jadwal:<nama>`. Tanggal data = tanggal jadwal − `date_offset`, jadi
run yang terlambat tetap memproses tanggal yang benar; kejadian yang terlewat berhari-hari hanya
ditembak sekali. Cakupan dievaluasi saat ditembak, jadi job baru yang cocok pola otomatis ikut.
Jadwal baru **nonaktif**.

> **Hindari ekspor dobel.** Selama `run_exporter.bat` masih terjadwal di Task Scheduler, jangan
> aktifkan jadwal aplikasi untuk job yang sama, dan jangan menjalankannya dari UI pada jam yang sama.
> Peralihan: buat jadwal → matikan task lama → aktifkan jadwal. Hanya boleh ada **satu** instance
> aplikasi (satu `jobs.db`) yang mengunggah ke S3.

**Notifikasi email** (`pipeline/notify.py`, halaman Pengaturan, admin):
- Dikirim saat run gagal/terputus, dan saat **anomali 0 baris**: job yang biasanya berisi data tapi N
  tanggal terakhir (default 3) kosong — sekali per kejadian.
- Penerima = daftar tetap + pembuat run. Akun SMTP hanya di `.env` (`SMTP_*`); isi `APP_BASE_URL`
  supaya email berisi link.
- Gagal kirim tidak memengaruhi run. Semua percobaan tercatat di tabel `notifications`; tombol
  "Kirim email uji" untuk memeriksa setelan SMTP.

**Ekspor Cepat** (halaman Ekspor Cepat): unduh `.parquet` dari browser tanpa membuat job dan tanpa S3,
maks `ADHOC_MAX_ROWS` (default 500.000). Viewer hanya dari job terdaftar & aktif — keputusan keamanan,
karena login exporter bisa membaca tabel sensitif; jangan dilonggarkan tanpa persetujuan pemilik.
Job bertemplate khusus admin (mis. `sp_job` yang menjalankan ETL) hanya untuk Admin. Operator/Admin
juga bisa dari tabel bebas. Setiap unduhan tercatat di `adhoc_exports`.

**Pemeliharaan harian** (`pipeline/maintenance.py`, oleh worker): backup `jobs.db` ke `BACKUP_DIR`
(simpan `BACKUP_KEEP` = 14 terakhir), hapus log run & log worker yang lebih tua dari
`LOG_RETENTION_DAYS` = 90 hari (log CLI tidak disentuh), hapus session kedaluwarsa. **Restore**: hentikan web & worker → salin
`backups/jobs_YYYYMMDD_HHMMSS.db` menjadi `jobs.db` → jalankan lagi.

**Monitoring**: `GET /api/health` (tanpa login) → `{"ok": true, "worker_online": true}`; HTTP 503
kalau database tidak bisa dibuka. `worker_online: false` = worker mati, run & jadwal tertahan.

## 11. Deploy

### Docker — Windows container (server produksi)

Server: Windows Server 2016, Windows container, process isolation (`docker info --format "{{.OSType}}"`
→ `windows`). Satu image (`Dockerfile.windows`) untuk dua container yang me-mount **folder data yang
sama** ke `C:\data`:

| Container | Perintah | Port |
|---|---|---|
| `parquet-exporter-web` | default (uvicorn) | `8084:8000` — 8000/8082/8083 sudah dipakai aplikasi lain |
| `parquet-exporter-worker` | `python worker.py` — **satu instance saja** | — |

Image berisi Python, Visual C++ Redistributable, ODBC Driver 17, dependensi terkunci, dan frontend hasil
build. `scripts/check_runtime.py` membuat build gagal kalau pyarrow atau ODBC Driver belum siap. `.env`,
`jobs.db`, `jobs.py`, `logs/`, `output/`, dan `backups/` tidak masuk image. Alasan teknis tiap langkah
ada di komentar `Dockerfile.windows`.

**Deploy pertama** (contoh: kode di `D:\APLIKASI\parquet-exporter`, data di
`D:\APLIKASI\parquet-exporter-data` — di luar folder kode supaya update tidak menyentuh data):

1. Ambil kode (git clone/pull).
2. Buat `.env` dari `.env.example`. Format `--env-file`: tanpa tanda kutip, tanpa komentar di belakang
   nilai. **Jangan** isi `JOBS_DB`, `LOG_DIR`, `RUN_LOG_DIR`, `OUTPUT_DIR`, `BACKUP_DIR` — image sudah
   mengarahkannya ke `C:\data`. Isi `APP_BASE_URL=http://<server>:8084`.
3. Pindahkan `jobs.db` lama (user, job, kunci S3, dan riwayat ikut). Hentikan web & worker di mesin
   lama, lalu buat salinan yang konsisten — menyalin `jobs.db` langsung bisa kehilangan data yang masih
   ada di file `-wal`:
   ```powershell
   python -c "import sqlite3; s = sqlite3.connect('jobs.db'); d = sqlite3.connect('jobs_pindah.db'); s.backup(d); d.close(); s.close()"
   ```
   Salin `jobs_pindah.db` menjadi `D:\APLIKASI\parquet-exporter-data\jobs.db`.
4. Build, lalu cek jaringan dari dalam container (DNS + TCP ke SQL Server, SSO, SMTP, S3, tanpa login):
   ```powershell
   docker build -f Dockerfile.windows -t parquet-exporter .
   docker run --rm --env-file .env parquet-exporter python scripts\check_network.py
   ```
   Nama host internal sering gagal di-resolve dari container, dan `--add-host` tidak berlaku di Windows
   container. Ambil IP-nya dengan `Resolve-DnsName <nama>` **di host server** (hasil dari PC lain bisa
   beda), build ulang dengan `--build-arg HOSTS_ENTRIES="<ip> <nama>;<ip> <nama>"`, lalu cek lagi. IP
   tidak ditulis ke file Git — simpan perintah build-nya di catatan server.
5. Jalankan:
   ```powershell
   $data = "D:\APLIKASI\parquet-exporter-data"
   docker run -d --name parquet-exporter-web --restart unless-stopped --env-file .env -v "${data}:C:\data" -p 8084:8000 parquet-exporter
   docker run -d --name parquet-exporter-worker --restart unless-stopped --env-file .env -v "${data}:C:\data" parquet-exporter python worker.py
   ```
6. Verifikasi dari PC lain (di Windows Server 2016 port container biasanya tidak bisa dibuka lewat
   `localhost` server itu sendiri): `http://<server>:8084/api/health` harus `worker_online: true` —
   bukti kedua container berbagi `jobs.db`. Lalu jalankan satu run uji dengan toggle **Upload ke S3**
   dimatikan. Buka port 8084 di firewall/Security Group bila perlu.
7. Matikan instance lama dan task `run_exporter.bat` yang tumpang tindih (§10).

**Update versi**: `git pull` → `docker build` (dengan `HOSTS_ENTRIES` yang sama) →
`docker rm -f parquet-exporter-web parquet-exporter-worker` → ulangi kedua `docker run` di langkah 5.
Lakukan saat tidak ada run yang berjalan; run yang terputus ditandai `interrupted` dan bisa diulang.
Skema `jobs.db` naik otomatis saat container start.

```powershell
docker exec parquet-exporter-worker python exporter.py --list                       # CLI di dalam container
docker exec parquet-exporter-worker python manage.py add-user <username.sso> --role admin
docker logs --tail 100 parquet-exporter-worker
```

Log, output lokal, dan backup harian ada di folder data host (`logs\`, `output\`, `backups\`).

### Tanpa Docker (Windows biasa)

Butuh akses jaringan ke SQL Server, S3, dan portal SSO, plus ODBC Driver 17. Ikuti §3, lalu jalankan
`run_web.bat` dan `run_worker.bat` sebagai service yang otomatis start saat boot:

```powershell
# Opsi A — NSSM (service sungguhan, restart otomatis kalau crash)
nssm install ParquetExporterWeb    "D:\path\LoadParquetProcess\run_web.bat"
nssm install ParquetExporterWorker "D:\path\LoadParquetProcess\run_worker.bat"
nssm start ParquetExporterWeb; nssm start ParquetExporterWorker

# Opsi B — Task Scheduler (tidak me-restart proses yang crash; pantau /api/health)
schtasks /Create /TN "ParquetExporter\Web"    /TR "\"D:\path\LoadParquetProcess\run_web.bat\""    /SC ONSTART /RU SYSTEM /RL HIGHEST
schtasks /Create /TN "ParquetExporter\Worker" /TR "\"D:\path\LoadParquetProcess\run_worker.bat\"" /SC ONSTART /RU SYSTEM /RL HIGHEST
```

`frontend/dist` tidak masuk Git: build di server (perlu Node.js) atau salin hasil build dari PC dev.

### HTTPS

Halaman login mengirim **password SSO** ke aplikasi ini. Lewat HTTP biasa, password itu berjalan tanpa
enkripsi di jaringan. Minimal batasi port web hanya untuk jaringan internal. Idealnya pasang reverse
proxy HTTPS (IIS URL Rewrite + ARR, atau nginx) di depan uvicorn, set `COOKIE_SECURE=true` dan
`APP_BASE_URL=https://…`. Tanpa Docker, jalankan web dengan `WEB_HOST=127.0.0.1` supaya hanya proxy
yang bisa mengaksesnya; uvicorn memakai header `X-Forwarded-*` dari 127.0.0.1, jadi IP klien untuk
batas percobaan login tetap benar.
