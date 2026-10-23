# SQL Server → Parquet → S3

Dokumen ini menjelaskan cara kerja pipeline ekspor beserta file pendukungnya. Tujuannya:
AI yang membaca file ini bisa langsung paham alur, batasan, dan aturan modifikasi
tanpa perlu membaca ulang seluruh kode.

Referensi kode ditulis sebagai `file.py` + nama fungsi (mis. `pipeline/export.py:run_job()`),
bukan nomor baris — nomor baris cepat basi setiap kali file berubah.

---

## 1. Apa yang dilakukan pipeline ini

SQL Server 2012 tidak punya dukungan Parquet native. Pipeline ini menjembatani:

```
SQL Server 2012           pipeline/export.py                   AWS S3
┌──────────────────┐      ┌────────────────────┐      ┌──────────────────────┐
│ EXEC dbo.AI_WM_  │      │ pyodbc fetchmany   │      │ gold/awl/            │
│ Assistant        │─────▶│   ↓ (streaming)    │─────▶│   recorded_year=…/   │
│ @function=N      │      │ pyarrow            │      │    part-00001.parquet│
│ @TargetDate=…    │      │ ParquetWriter      │      └──────────────────────┘
└──────────────────┘      └────────────────────┘               ▲
                                    │                          │
                                    └──▶ output/*.parquet (lokal)
```

Data ditarik **streaming** (batch demi batch), bukan dimuat seluruhnya ke memori,
sehingga tabel berukuran jauh di atas kapasitas RAM tetap bisa diekspor.

Konteks bisnis: data monitoring air (water management) perkebunan — AWL, ARS,
curah hujan manual, TMAT/TMAS, plus tabel master (estate, block, device IoT).
Tujuan akhirnya dataset untuk AI/ML, karena itu presisi angka dijaga ketat
(`decimal`, bukan `float`) dan ada field `labels` untuk menandai kolom target model.

---

## 2. Peta file

```
LoadParquetProcess/
├── exporter.py          CLI ekspor (python exporter.py ...)            ┐
├── worker.py            eksekutor antrian run + scheduler (Web UI)     │ skrip yang DIJALANKAN
├── manage.py            kelola user Web UI (admin pertama)             │ langsung -- tetap di
├── migrate_jobs.py      bootstrap jobs.db dari jobs.py + parity check  │ folder proyek
├── run_exporter.bat / run_web.bat / run_worker.bat                     ┘
├── pipeline/            KODE INTI (library, tidak dijalankan langsung)
├── webapp/              API FastAPI (Web UI)
├── frontend/            React + Tailwind (build -> frontend/dist)
├── scripts/             helper: find_python.bat, check_runtime.py, check_network.py
├── Dockerfile.windows, .dockerignore   image Windows container (bagian 11)
├── jobs.db, .env, jobs.py      milik environment, gitignored
└── output/, logs/, backups/    hasil run, gitignored
```

Aturannya: skrip yang dijalankan manusia / Task Scheduler / service ada di folder proyek; semua
logika yang dipakai bersama ada di `pipeline/` (diimpor sebagai `from pipeline.xxx import ...`).
Semua perintah dijalankan dari folder proyek (file `.bat` melakukan `cd` ke folder sendiri).

| File | Peran | Kapan diedit |
|---|---|---|
| `jobs.db` | **Daftar ekspor** (SQLite, gitignored). Satu baris tabel `jobs` = satu dataset: nama + template + argumen JSON + aktif/nonaktif + catatan. **Inilah yang dibaca exporter & worker.** | **Lewat Web UI** (halaman Daftar Job → Tambah/Edit) sejak Fase 2 — lihat bagian 9 |
| `jobs.py` | Format lama daftar job (gitignored). Sejak Fase 2 hanya untuk **bootstrap** environment baru (`migrate_jobs.py`) & arsip. `from job import Job` di `jobs.py` lama tetap jalan (`migrate_jobs.py` memasang alias ke `pipeline.job`). | Tidak lagi — `migrate_jobs.py --replace` ditolak kalau sudah ada edit lewat UI |
| `exporter.py` | **CLI** — argumen, loop tanggal × job, ringkasan & exit code. Mesinnya dipanggil dari `pipeline/export.py`. | Saat menambah opsi CLI |
| `migrate_jobs.py` | Migrasi `jobs.py` → `jobs.db` + **parity check** (lihat di bawah). | Saat menambah template baru (`HELPER_TO_TEMPLATE`) |
| `pipeline/export.py` | **Mesin eksekusi** (dipakai CLI, worker, Web UI): deteksi schema, batching, tulis parquet, upload S3, setup log. | Jarang — hanya kalau mekanismenya berubah |
| `pipeline/job_templates.py` | **Template job** (pindahan helper `jobs.py`): `bronze_daily*`, `bronze_master`, `bronze_arcsde_view`, `sp_job`, `custom_sql` + penyusun S3 key `s3_daily`/`s3_weekly`/`s3_master`. Generik — masuk Git. | Saat butuh pola query baru |
| `pipeline/job_validation.py` | Aturan validasi job dari Web UI: identifier dicek ke SQL Server, keunikan nama/output/folder S3, template khusus admin, kunci path S3. | Saat aturan berubah / template baru |
| `pipeline/job_store.py` | Akses `jobs.db`: skema tabel (`_MIGRATIONS`), `load_job_rows()`, `build_jobs(target_date, rows)`, `default_target_date()`. | Saat struktur tabel berubah |
| `pipeline/job.py` | `@dataclass Job` — definisi struktur satu job. | Hanya kalau menambah field baru ke Job |
| `pipeline/config.py` | Connection string, `BATCH_SIZE`, `COMPRESSION`, path (`OUTPUT_DIR`, `LOG_DIR`, `JOBS_DB`, `RUN_LOG_DIR`, `BACKUP_DIR` — default relatif ke folder proyek, bisa diganti lewat `.env`), `S3_PREFIX`, kredensial AWS, setelan Web UI. Semua rahasia dibaca dari `.env` via `python-dotenv`. | Saat setelan global berubah |
| `pipeline/run_store.py`, `scheduler.py`, `notify.py`, `maintenance.py`, `app_settings.py` | Antrian & riwayat run, jadwal, email notifikasi, pemeliharaan harian, setelan non-rahasia — bagian 8 & 10. | Mekanisme Web UI / operasional |
| `.env` | Kredensial nyata (tidak masuk Git). Contohnya di `.env.example`. | — |
| `webapp/`, `worker.py`, `manage.py`, `frontend/` | **Web UI** (Fase 1) — lihat bagian 8. | — |

### Daftar job: `jobs.py` → `jobs.db`

```
jobs.py ──(migrate_jobs.py: bedah AST + parity check)──▶ jobs.db ──(job_store.build_jobs)──▶ Job ──▶ exporter.py
```

- Kolom `template` = kunci `TEMPLATES` di `pipeline/job_templates.py`; `args` = argumen template (JSON).
  Semua nilai yang menentukan query/path S3 (db, layer, module, dataset, nama file, nama SP,
  linked server) disimpan **eksplisit per job** — default helper lama ikut ditulis saat
  migrasi, jadi mengubah default di template tidak diam-diam memindahkan path S3 job lama.
- Satu-satunya setelan global yang ikut menentukan path S3: `S3_PREFIX` (`pipeline/config.py`, dari `.env`).
- Entri yang di-comment di `jobs.py` diimpor sebagai `enabled=0`. Komentar prosa tepat di atas
  entri disimpan ke `notes` (komentar judul grup menempel ke job pertama grup itu).
- **Parity check**: Job dari database dibandingkan dengan Job dari `build_jobs()` `jobs.py` asli
  untuk beberapa tanggal uji (termasuk kabisat & pergantian tahun) — query, params, output,
  overrides, labels, needs_date, S3 key, dan urutan job aktif harus identik byte-per-byte.
  Job nonaktif dibandingkan dengan salinan `jobs.py` yang entri-nya di-uncomment. Ada beda →
  database tidak ditulis (exit code 2).
- `custom_sql.params` memakai `{"$var": "target_date"}` / `{"$var": "next_date"}` untuk nilai
  tanggal. `overrides` disimpan sebagai teks `str(tipe)` (mis. `"decimal128(18, 4)"`) dan
  dibaca lewat whitelist `parse_arrow_type()`, bukan objek pyarrow / eval.

```powershell
python migrate_jobs.py --dry-run   # analisis + parity saja, tanpa menulis
python migrate_jobs.py             # isi jobs.db pertama kali (environment baru)
python migrate_jobs.py --replace   # sinkron ulang dari jobs.py -- DITOLAK kalau sudah ada edit lewat Web UI
                                   # (tambahkan --discard-ui-edits kalau memang mau membuang edit UI)
python migrate_jobs.py --check     # bandingkan isi jobs.db dengan jobs.py (beda = wajar setelah edit UI)
```

Struktur `Job` (`pipeline/job.py`):

| Field | Tipe | Keterangan |
|---|---|---|
| `name` | `str` | Nama pendek, unik. Dipakai `--only` dan label log |
| `query` | `str` | SELECT biasa **atau** `EXEC` stored procedure yang diakhiri SELECT |
| `output` | `str` | Nama file `.parquet` lokal (relatif ke `OUTPUT_DIR`) |
| `params` | `list` | Nilai untuk tiap `?` di query, **sesuai urutan** |
| `overrides` | `dict[str, pa.DataType]` | Paksa tipe Arrow untuk kolom tertentu |
| `labels` | `list[str]` | Kolom target/label untuk dokumentasi dataset ML (hanya dicatat ke log) |
| `s3_key` | `Callable[[dict], str] \| None` | Fungsi penghasil S3 key. `None` = tidak diupload |
| `needs_date` | `bool` | `False` = data master, tidak tergantung tanggal |

---

## 3. Alur eksekusi end-to-end

### `main()` — `exporter.py`

1. **Parse argumen** (`argparse`).
2. **Baca daftar job** dari `jobs.db` SEKALI di awal run (`load_job_rows`) — semua tanggal di
   mode range memakai snapshot yang sama. Database belum ada → error + exit `1`.
   **`--list`**: cetak semua job (yang nonaktif ditandai `[nonaktif]`) lalu keluar. Job
   dibangun untuk tanggal default, jadi `--list` tidak memperhitungkan `--date`.
3. **Validasi tanggal**: `--date` dan `--start-date/--end-date` tidak
   boleh dipakai bersamaan; `--start-date` dan `--end-date` harus berpasangan;
   `end >= start`. Tanpa opsi tanggal apa pun → `default_target_date()` (`pipeline/job_store.py`):
   kemarin menurut kalender WIB (dikunci `Asia/Jakarta`, bukan timezone OS).
4. **Bangun `date_list`**. `is_range = len(date_list) > 1`.
5. **Validasi `--only`** (`job_matches()`): nama tak dikenal hanya diperingatkan
   (warning) dan diabaikan; error hanya kalau **tidak ada satu pun** nama yang cocok.
6. **Siapkan klien S3** (`make_s3_client()`): dilewati kalau `--skip-s3`, atau kalau
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` belum lengkap
   (hanya warning, proses tetap lanjut tanpa upload).
7. **Satu koneksi pyodbc** dibuka untuk **seluruh** run — semua job, semua tanggal.
8. **Loop tanggal → loop job**:
   - `build_jobs(date_str, job_rows)` dipanggil ulang **per tanggal** (hanya job aktif),
     sehingga seluruh S3 key dan `params` ikut menyesuaikan tanggal itu. `--only` hanya
     bisa memilih job aktif — job nonaktif tidak ikut walau cocok pola wildcard.
   - Job master (`needs_date=False`) di mode range dilewati kalau sudah pernah jalan,
     dilacak lewat `date_independent_done`.
   - `date_suffix` diisi **hanya** kalau `is_range and job.needs_date` — dipakai
     agar file lokal per tanggal tidak saling menimpa.
   - Kegagalan satu job ditangkap, dicatat, lalu **lanjut ke job berikutnya**, kecuali
     `--stop-on-error`.
9. **Exit code**: `0` semua sukses, `2` ada job gagal, `1` error argumen
   atau koneksi.

### `run_job()` — `pipeline/export.py`

1. **Tentukan tujuan tulis**:
   - `write_to_s3` = job punya `s3_key` **dan** klien S3 tersedia.
   - `use_local_file` = `not (skip_local and write_to_s3)`. Artinya `--skip-local`
     hanya efektif kalau job memang punya tujuan S3; kalau tidak, file **tetap**
     disimpan lokal supaya hasil query tidak hilang percuma.
   - Kalau tidak menulis lokal, sink-nya `io.BytesIO()` — parquet dibentuk di memori
     lalu langsung `upload_fileobj`.
2. **Eksekusi query**: `params` dikirim terpisah ke `cursor.execute()`,
   jadi aman dari SQL injection.
3. **Lompati resultset non-SELECT** (`advance_to_resultset_with_columns()`): SP menjalankan
   `EXEC sp_ETL_LoadSilver…` / `sp_ETL_LoadGold…` sebelum SELECT akhir, yang
   menghasilkan resultset tanpa kolom. Loop `cursor.nextset()` maju sampai
   `cursor.description` tidak `None`. Kalau habis tanpa menemukan resultset berkolom
   → `return 0`, **tanpa membuat file apa pun**.
4. **Bangun schema** (`build_schema()`), lalu dicatat ke log.
5. **Loop tulis**:
   ```python
   writer = pq.ParquetWriter(sink, schema, compression=compression)
   while True:
       rows = cursor.fetchmany(batch_size)
       if not rows: break
       if first_row is None:
           first_row = dict(zip((f.name for f in schema), rows[0]))
       writer.write_table(rows_to_table(rows, schema))
   ```
   Setiap `write_table()` menghasilkan **satu row group** di file parquet.
6. **Upload S3**: hanya kalau `total > 0`. `job.s3_key(first_row)` dipanggil
   di sini — inilah kenapa penyusun key bisa membaca isi data (dipakai oleh `s3_weekly`).

---

## 4. Mekanisme kunci

### Deteksi schema otomatis — `pipeline/export.py:arrow_type_from_description()`

Tipe kolom dibaca dari metadata pyodbc (`cursor.description`), bukan ditebak dari nilai:

| Tipe SQL Server / Python | Tipe Arrow | Catatan |
|---|---|---|
| `decimal` / `numeric` (`decimal.Decimal`) | `decimal128(p, s)` | **Presisi terjaga, bukan float.** Kalau metadata presisinya kosong → default `38` |
| `bit` (`bool`) | `bool_()` | |
| `int` | `int64()` | |
| `float` | `float64()` | |
| `datetime` / `datetime2` | `timestamp("us")` | **Naive** — tanpa timezone, diasumsikan WIB |
| `date` | `date32()` | |
| `time` | `time64("us")` | |
| `bytes` / `varbinary` | `binary()` | |
| lainnya (`str`, `uniqueidentifier`) | `string()` | Fallback |

Override manual per kolom lewat `overrides` di Job — key-nya **nama kolom persis**
seperti yang dikembalikan query (`build_schema()` mencocokkan `overrides.get(col_name)`).

### Kolom audit `_ingested_at` — `pipeline/export.py:run_job()`

Setiap file parquet (semua job, bronze DAN gold, otomatis, tidak perlu dikonfigurasi per
job) diberi kolom tambahan **`_ingested_at`** (`timestamp("us", tz="UTC")`, tz-aware) di
posisi paling akhir — bukan dari SQL Server, tapi di-append pipeline (`schema.append(...)`)
setelah `build_schema()`. Ini beda dari partisi `recorded_*`/`snapshot_date` (lihat bagian 5):
- Partisi = tanggal **bisnis/proses** (`target_date`), dipakai untuk partition pruning.
- `_ingested_at` = jam **wall-clock UTC** sebenarnya saat `run_job()` menjalankan job itu.

Satu nilai `_ingested_at` di-broadcast ke SEMUA baris dalam satu file (dihitung sekali di
awal `run_job()`, bukan per-baris/per-batch) — karena ini metadata "kapan file ini
ditulis", bukan data yang datang dari SQL Server. Prefix underscore sengaja dipakai supaya
jelas ini kolom teknis, bukan kolom sumber (kolektif dengan `data_field_names` yang dijaga
terpisah dari `schema` penuh, supaya `first_row` — dipakai `s3_key` dinamis, lihat di bawah
— tidak pernah menyertakan kolom ini).

**Batasan yang harus diketahui:** karena desain overwrite-per-partition (lihat bagian 5,
keputusan #2), `_ingested_at` cuma mencerminkan **load TERAKHIR yang sukses** untuk partisi
itu — bukan riwayat semua percobaan sebelumnya kalau job yang sama di-retry berkali-kali.
Bukan pengganti run-log/audit-trail eksekusi (job apa, kapan, gagal/sukses, berapa baris) —
itu kebutuhan berbeda yang belum ada di pipeline ini.

### Batching — `-b` / `--batch`

Default `BATCH_SIZE = 100_000` (`pipeline/config.py`). Angka ini menentukan dua hal sekaligus:
puncak pemakaian memori (`batch × lebar baris`) dan ukuran row group parquet.
Turunkan kalau memori terbatas; naikkan untuk data besar agar row group lebih efisien
dibaca Athena/Spark. Hindari batch sangat kecil untuk data besar — hasilnya banyak
row group kecil yang justru memperlambat pembacaan.

### `first_row` dan S3 key dinamis

`s3_key` adalah **callable** yang menerima dict `{nama_kolom: nilai}` dari **baris
pertama batch pertama**. Ini dipakai `s3_weekly` di `pipeline/job_templates.py` untuk mengambil
`week_year` / `week_month` / `week_of_month` langsung dari hasil query — batas minggu
ditentukan tabel `dbo.T_PZO_Week_Temp` di SQL Server, jadi tidak boleh dihitung ulang
di Python. Helper lain mengabaikan argumen ini (`lambda _first_row: key`).

### Mode range

```powershell
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15
```

- Job harian dijalankan **sekali per tanggal**; file lokalnya diberi sufiks tanggal
  (`gold_awl_readings_20260701.parquet`) lewat `pipeline/export.py:resolve_output()`.
- Job master dijalankan **sekali saja** untuk seluruh range.

---

## 5. Layout S3

Konvensi ditentukan oleh penyusun S3 key di `pipeline/job_templates.py` (`s3_daily`/`s3_weekly`/
`s3_master`). `S3_PREFIX` global di `pipeline/config.py` (dari `.env`, default `""` = `layer` langsung
di root bucket); `layer`, `module`, `dataset`, dan nama file disimpan per job di `jobs.db`.
Bucket: `s3://<nama-bucket-s3-anda>/` (`S3_BUCKET_NAME` di `.env`). Contoh path lengkap tiap job
tampil di Web UI (detail job & preview wizard).

Segmen `module` dan `dataset` ditulis sebagai **folder polos**; hanya segmen partisi tanggal yang
berbentuk `kunci=nilai`:

```
[prefix/]{layer}/{module}/{dataset}/recorded_year=YYYY/recorded_month=MM/recorded_day=DD/{file}      harian
[prefix/]{layer}/{module}/{dataset}/recorded_year=YYYY/recorded_month=MM/recorded_week=W{n}/{file}   mingguan
[prefix/]{layer}/{module}/master_{dataset}/snapshot_date=YYYY-MM-DD/{file}                           master
```

```
bronze/ars/hk_transactions/recorded_year=2026/recorded_month=07/recorded_day=20/bronze_ars_hk_transactions.parquet
└─┬──┘ └┬┘ └──────┬──────┘ └────────────────────────┬─────────────────────────┘ └───────────────┬────────────────┘
layer module   dataset                     partisi Hive-style                               nama file
```

- **`module` opsional.** Kosong → segmen dilewati: `{layer}/{dataset}/...` (dipakai beberapa job gold
  lama). Wizard Web UI memperingatkan kalau modul kosong.
- **Nama segmen case-sensitive** — di S3 `pzo/` dan `PZO/` adalah dua folder berbeda. Job baru memakai
  huruf kecil (`hms`, `iot`, `pzo`, ...); beberapa job gold lama memakai huruf besar dan dibiarkan
  karena path-nya sudah terkunci (lihat bagian 9).
- **Nama file**: template bronze memakai **nama file output job** (default `{nama_job}.parquet`;
  `bronze_master` bisa memakai `s3_file_name` lain untuk job lama yang terlanjur diupload dengan nama
  generik). `sp_job`/`custom_sql` memakai `file_name` di spesifikasi S3-nya, default
  `DEFAULT_PART_FILE = "part-00001.parquet"`.
- Draf awal sempat merencanakan segmen `source=`/`dataset=` berbentuk `kunci=nilai`. Itu **tidak**
  diterapkan dan jangan "diperbaiki" di kode — semua path produksi sudah terkunci; mengubah bentuk
  path = memindahkan seluruh data di S3.

Lima keputusan desain yang **jangan diubah tanpa alasan kuat**:

1. **Format `kunci=nilai` pada folder partisi tanggal.** Inilah yang membuat Athena/Spark/Glue
   crawler otomatis mengenali `recorded_year` dkk sebagai kolom partisi, sehingga
   `WHERE recorded_year=2026 AND recorded_month=07` hanya memindai folder relevan
   (partition pruning).

2. **Nilai partisi diambil dari `target_date`, bukan jam run.** Konsekuensinya
   re-run/backfill tanggal yang sama menimpa partisi yang sama → pipeline idempoten.

3. **Nama file konstan antar-run.** Athena/Spark membaca **semua** file dalam satu folder
   partisi lalu menggabungkannya. Karena tiap job mengembalikan data **penuh** untuk
   tanggalnya (bukan delta), nama file yang auto-increment atau ber-timestamp antar-run akan
   membuat data **terhitung dobel**. Nama konstan = overwrite = benar. Nama file boleh berbeda
   per job, TAPI harus tetap sama antar-run untuk partisi yang sama. Karena alasan yang sama,
   dua job tidak boleh berbagi folder dataset walau nama filenya beda (dicek
   `job_validation`, bagian 9).

4. **Master diberi awalan `master_`.** Tanpa itu, transaksional `manual_wl_tmat` dan
   master `manual_wl_tmat` jatuh ke folder yang sama dengan skema partisi berbeda
   (`recorded_*` vs `snapshot_date`) — Glue crawler akan gagal menyimpulkan skema.
   Aturan ini tetap berlaku di dalam segmen `module` yang sama (mis. `pzo/manual_wl_tmat`
   vs `pzo/master_manual_wl_tmat`).

5. **Segmen `module` letaknya SELALU tepat di bawah `layer`, di atas `dataset`.**
   Konsisten di bronze/silver/gold supaya satu sistem (mis. ARS) gampang ditelusuri
   lintas layer. Kalau modul sebuah tabel belum jelas, lebih baik dikosongkan daripada
   ditebak — salah tebak modul di path S3 produksi sulit dideteksi (silent, sama seperti
   tabrakan `s3_key` biasa), dan setelah upload pertama path terkunci; memindahkannya
   meninggalkan data lama di path lama.

Master memakai **snapshot per tanggal**, bukan satu file yang di-overwrite, agar
dataset ML reproducible (model yang dilatih dengan master kondisi 20 Juli tetap bisa
direproduksi walau blok/estate berubah) dan agar terlihat kapan sebuah entitas mulai
atau berhenti ada. Cukup **satu** level partisi karena tabel master kecil dan dibaca
dengan pola "ambil `MAX(snapshot_date)`", bukan di-scan per rentang.

---

## 6. Referensi CLI

| Flag | Efek |
|---|---|
| *(tanpa opsi)* | Jalan untuk tanggal default: kemarin (kalender WIB) |
| `--date YYYY-MM-DD` | Satu tanggal tertentu |
| `--start-date` + `--end-date` | Rentang tanggal, inklusif |
| `--only NAMA…` | Batasi ke job tertentu -- tiap item boleh nama persis ATAU pola wildcard (`*`, `?`, lihat `job_matches()` di `exporter.py`), mis. `--only "bronze_hms_*"` untuk satu grup sekaligus |
| `-b`, `--batch N` | Baris per fetch / per row group (default `100_000`) |
| `-c`, `--compression` | `zstd` (default), `snappy`, `gzip`, `none` |
| `--list` | Cetak daftar job (termasuk yang nonaktif) lalu keluar |
| `--stop-on-error` | Berhenti di kegagalan pertama |
| `--skip-s3` | Simpan lokal saja |
| `--skip-local` | Upload langsung dari memori; job tanpa tujuan S3 tetap disimpan lokal |

```powershell
python exporter.py --list
python exporter.py --date 2026-07-20 --only awl ars
python exporter.py --date 2026-07-20 --only "bronze_hms_*" --skip-s3   # satu grup job sekaligus (kutip pola di shell)
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15 --stop-on-error
python exporter.py --date 2026-07-20 --skip-s3 -b 20000
```

---

## 7. Aturan saat memodifikasi

1. **Tambah/ubah ekspor = lewat Web UI** (Daftar Job → Tambah Job / Edit), bukan lagi `jobs.py`.
   Jangan sentuh `pipeline/export.py`. Pola query yang belum ada = template baru di
   `pipeline/job_templates.py` + spesifikasinya di `job_validation.TEMPLATE_SPECS` (role minimal) +
   aturan validasinya + `TEMPLATE_FIELDS` di `frontend/src/lib/jobs.js`.
2. **Placeholder parameter pakai `?`, bukan `@param`.** pyodbc memakai gaya qmark;
   `params` harus berurutan sesuai posisi `?`.
3. **`SET NOCOUNT ON;` wajib di awal query SP**, supaya pesan "N rows affected" tidak
   muncul sebagai resultset pengganggu.
4. **`name`, `output`, dan hasil `s3_key` harus unik antar job.** Dua job dengan
   `output` sama akan saling menimpa file lokal; `s3_key` sama akan saling menimpa
   objek S3 — keduanya senyap, tanpa error.
5. **Jangan membuat nama file S3 dinamis** (timestamp, UUID, atau nomor berurut) selama
   tiap job masih mengembalikan data penuh per tanggal. Lihat poin 3 di bagian 5.
6. **Kredensial hanya dari environment/`.env`.** Jangan pernah hard-code di `pipeline/config.py`.
7. **`overrides`** ditulis sebagai objek `pa.DataType` di `jobs.py` (mis. `pa.decimal128(18, 4)`)
   dan tersimpan sebagai teks `str(tipe)` di `jobs.db`; key-nya nama kolom persis.
8. Kalau menambah field ke `Job`, beri **default** di `pipeline/job.py` agar job lama tidak rusak.
9. **`pipeline/job_templates.py` masuk Git — jangan tulis nilai spesifik environment di sana** (nama
   database/SP, IP linked server, nama tabel); jadikan argumen template, nilainya tersimpan
   per job di `jobs.db`. Whitespace string query di template sengaja sama persis dengan
   helper lama — mengubahnya membuat parity check gagal.
10. **Modul baru yang bukan skrip untuk dijalankan langsung masuk `pipeline/`** (atau `webapp/` untuk
    kode khusus web), bukan folder proyek. Impor antarmodul pakai nama publik — jangan mengimpor
    nama berawalan `_` dari modul lain; kalau dibutuhkan bersama, jadikan publik.

---

## 8. Web UI (Fase 1)

Tim bisa menjalankan & memantau ekspor dari browser tanpa CLI. Tambah/ubah job lewat UI: bagian 9.

```
Browser (React) ──▶ webapp/ (FastAPI, :8000) ──▶ jobs.db (WAL) ◀── worker.py ──▶ SQL Server / S3
                        │ login                       ▲ runs / run_items
                        ▼                             │
                  SSO portal (gettoken, cor/user)     logs/runs/run_<id>.log ──▶ log live di UI
```

| Komponen | Peran |
|---|---|
| `webapp/main.py` | App FastAPI: router `/api/...` + menyajikan `frontend/dist` (SPA). Dokumentasi API: `/api/docs` |
| `webapp/routes_auth.py` | Login SSO → session aplikasi sendiri (cookie HttpOnly, hash sha256 di tabel `sessions`). Token SSO hanya dipakai sesaat saat login. Batas 5x gagal / 10 menit per (IP, username) |
| `webapp/routes_runs.py` | Preview (rencana item + path S3 yang akan ditimpa), buat run, riwayat, detail, log (polling per offset), cancel, retry |
| `webapp/routes_jobs.py`, `routes_schema.py`, `catalog.py` | Daftar/detail/tambah/ubah/uji coba job · penjelajah tabel SQL Server (bagian 9) |
| `webapp/routes_users.py` | Kelola user (admin) |
| `pipeline/run_store.py` | Antrian & riwayat run di `jobs.db`. Klaim run pakai `BEGIN IMMEDIATE`; dua run yang memakai job yang sama tidak pernah jalan bersamaan |
| `worker.py` | Proses terpisah: ambil run `queued`, eksekusi item satu per satu lewat `pipeline/export.py:run_job()`, heartbeat tiap 15 detik. Run yang heartbeat-nya basi > 90 detik → `interrupted` |
| `manage.py` | CLI kelola user (terutama admin pertama) |
| `frontend/` | React 19 + Tailwind 4 + React Router 7 (Vite). `npm run build` → `frontend/dist`. Halaman: Beranda (ringkasan), Jalankan, Riwayat Run, Daftar Job, Users |
| `frontend/src/index.css` | **Design token** (`@theme`): hijau `brand-*` (aksi utama `#059669`, sidebar `#064E3B`, menu aktif `#10B981`, banner mint `#ECFDF5`), teks `gray-800 #1F2937` / `gray-500 #6B7280`, latar `gray-100 #F3F4F6`, soft blue `blue-50` untuk ikon. Hex persis didefinisikan ulang karena palet bawaan Tailwind v4 (OKLCH) sedikit berbeda. Tampilan mode terang saja. Komponen dasar di `src/components/ui.jsx` |

**Skema database v2** (`job_store._MIGRATIONS`, dijalankan otomatis saat web/worker/manage start):
`users`, `sessions`, `runs`, `run_items` (menyimpan SNAPSHOT `template`+`args` job saat run dibuat —
audit "dijalankan dengan definisi apa"), `workers` (indikator "Worker aktif" di UI).
Struktur berubah = TAMBAH elemen baru di `_MIGRATIONS`, jangan ubah elemen lama.

**Role**: `viewer` (lihat), `operator` (+ jalankan/batalkan/ulangi run), `admin` (+ kelola user).
Login hanya untuk username yang terdaftar & aktif di tabel `users`. Dari profil SSO hanya nama
lengkap & email yang disimpan — NIK / No. KTP di response `cor/user` diabaikan.

**Semantik run = CLI**: job harian sekali per tanggal (file lokal bersufiks tanggal di mode range),
job master sekali dengan snapshot tanggal awal (`run_store.plan_items`). Beda kecil: master yang
gagal tidak dicoba ulang di tanggal berikutnya — pakai "Ulangi yang belum berhasil" (memakai
definisi job TERBARU). Cancel saat running = job yang sedang jalan diselesaikan, sisanya dibatalkan.
Default opsi sama dengan `run_exporter.bat`: upload S3 ya, simpan lokal tidak.

### Menjalankan

```powershell
pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..       # sekali, dan setiap frontend berubah
python manage.py add-user <username.sso> --role admin  # admin pertama (sekali)
run_web.bat        # atau: python -m uvicorn webapp.main:app --host 0.0.0.0 --port 8000
run_worker.bat     # atau: python worker.py   (proses kedua, SATU instance per server)
```

Pengembangan frontend: `python -m uvicorn webapp.main:app --reload` + `cd frontend; npm run dev`
(Vite :5173 mem-proxy `/api` ke :8000).

### Deploy ke server (Windows)

Langkah di bawah untuk menjalankan langsung di Windows (tanpa Docker). **Server Docker produksi: pakai
Windows container — bagian 11.**

1. Server butuh akses jaringan ke SQL Server, S3, dan SSO portal; ODBC Driver 17 terpasang.
2. `.env`: kredensial seperti biasa + `SSO_BASE_URL`; `COOKIE_SECURE=true` kalau diakses lewat HTTPS.
3. `python migrate_jobs.py` (buat `jobs.db` di server) → `python manage.py add-user ... --role admin`.
4. `frontend/dist` tidak masuk Git: build di server (perlu Node.js) atau salin hasil build dari PC dev.
5. Jalankan `run_web.bat` dan `run_worker.bat` sebagai service yang otomatis start saat boot
   (mis. NSSM, atau Task Scheduler "At startup" + "Run whether user is logged on or not").
6. Buka port web di firewall. Disarankan HTTPS lewat reverse proxy (IIS/nginx) di depan uvicorn —
   lihat bagian 10 untuk detailnya.

**Catatan**: jadwal `run_exporter.bat` di Task Scheduler tetap berjalan terpisah dan TIDAK tercatat di
riwayat Web UI (dipindah ke dalam aplikasi di Fase 3). Hindari menjalankan job yang sama dari UI
pada jam jadwal CLI — keduanya menimpa file/partisi S3 yang sama.

## 9. Kelola job lewat Web UI (Fase 2)

Daftar job sekarang diedit lewat Web UI — `jobs.db` adalah sumber kebenaran, `jobs.py` hanya
arsip/bootstrap. Setiap perubahan tercatat di tabel `job_history` (siapa, kapan, sebelum/sesudah).

**Hak akses**: Viewer hanya melihat. Operator & Admin bisa tambah/ubah/aktif-nonaktif job bronze
(`bronze_daily*`, `bronze_master`, `bronze_daily_join`). Template `sp_job` (menjalankan ETL Load =
MENULIS ke SQL Server), `bronze_arcsde_view` (linked server), dan `custom_sql` khusus Admin
(`job_validation.TEMPLATE_SPECS`).

**Wizard tambah job** (`frontend/src/pages/JobFormPage.jsx`):
1. Pilih database & tabel dari SQL Server (`/api/schema/*`, hanya metadata, cache 2 menit).
2. Jenis data: Harian / Master / Harian via JOIN. Untuk harian, template **dipilih otomatis dari
   tipe & isi kolom tanggal** (`webapp/catalog.detect_date_format`, minimal 75% contoh cocok):
   datetime → `bronze_daily`; teks `YYYY-MM-DD` → `bronze_daily_strdate` (rentang); teks format
   lain (mis. `DD.MM.YYYY`) → `bronze_daily_strdate_eq` (kesetaraan); bigint 13 digit →
   `bronze_daily_epoch_ms`. Format tak dikenal → tidak bisa disimpan.
3. Modul/dataset/nama/file disarankan otomatis dari nama tabel; path S3 tampil live.
4. **Uji coba** (`/api/jobs/test-run`): `SET ROWCOUNT n` + transaksi yang SELALU di-rollback +
   query timeout 90 detik + maks 2 bersamaan. Tidak menulis file / upload. Override tipe kolom
   bisa dipilih langsung dari hasil schema.
5. Simpan — default **nonaktif**; "Aktifkan sekarang" hanya bisa kalau uji coba berhasil untuk
   isian yang sama. Job aktif bernama `bronze_*` otomatis ikut jadwal `run_exporter.bat`.

**Aturan yang dijaga sistem** (`job_validation.validate_job`, dipanggil live & saat simpan):
- Identifier (db/tabel/kolom/alias) harus identifier SQL biasa, bukan kata kunci T-SQL, DAN ada
  di SQL Server — template merangkai query dengan f-string, jadi ini yang mencegah injeksi SQL.
- Nama job, file output, dan **folder dataset S3** unik antar job (dua job satu folder = terbaca
  satu tabel di Athena/Glue walau nama file beda).
- **Kunci path S3** (`jobs.s3_locked_at`): terisi saat upload S3 pertama berhasil (CLI & worker,
  `job_store.mark_s3_uploaded`); job hasil impor jobs.py langsung terkunci. Path terkunci hanya
  boleh dipindah Admin dengan konfirmasi eksplisit (`confirm_s3_move`) — data lama tertinggal di
  path lama, kunci dilepas sampai upload berikutnya.
- Nama job tidak bisa diubah setelah dibuat (dipakai riwayat run & `--only`).

**Temuan performa** (`bronze_daily_strdate_eq`): pyodbc mengirim `str` sebagai NVARCHAR; dibanding
kolom VARCHAR, SQL Server mengonversi SELURUH kolom → index tidak terpakai. Terukur di tabel feed
SAP 64 jt baris: 70 detik vs 0,2 detik dengan `CAST(? AS VARCHAR(32))`, hasil sama. Template lama
`bronze_daily_strdate` tidak diubah (parity) — untuk tabel feed SAP pakai wizard (format
`DD.MM.YYYY` terdeteksi → `strdate_eq`).

**Skema database v3**: kolom `jobs.s3_locked_at`, `jobs.updated_by`, tabel `job_history`. Migrasi
otomatis saat web/worker/CLI start (CLI juga menjalankan `init_db`, supaya pencatatan kunci S3 tidak
gagal di mesin yang belum pernah menjalankan web).

## 10. Operasional (Fase 3)

### Jadwal — `pipeline/scheduler.py` (halaman **Jadwal**)

Jadwal = jam (WIB) harian/mingguan + cakupan job (pola nama seperti `bronze_*` / modul / daftar job)
+ opsi run. Thread scheduler di dalam `worker.py` (tiap 20 detik) mengubah jadwal yang jatuh tempo
menjadi **run biasa** di antrian — tercatat di Riwayat Run atas nama `jadwal:<nama>`.
- Tanggal data = tanggal kalender WIB **saat jadwal** − `date_offset` (bukan jam tembak), jadi run yang
  terlambat tetap memproses tanggal yang benar. Kejadian yang terlewat berhari-hari hanya ditembak
  SEKALI (yang terakhir) — sisanya backfill manual.
- Klaim jadwal di `BEGIN IMMEDIATE` → tidak ada tembakan dobel walau worker lebih dari satu.
- Cakupan dievaluasi saat ditembak → job baru yang cocok pola otomatis ikut.
- Jadwal baru **default nonaktif**. Selama `run_exporter.bat` di Task Scheduler masih aktif, jangan
  aktifkan jadwal yang mencakup job yang sama (ekspor dobel). Peralihan: buat jadwal → matikan task
  lama → aktifkan jadwal.

### Notifikasi email — `pipeline/notify.py` (halaman **Pengaturan**, admin)

| Kejadian | Isi | Penerima |
|---|---|---|
| Run gagal / terputus | Daftar eksekusi gagal + error + link run | Penerima tetap + pembuat run (run manual) |
| Anomali 0 baris | Job yang pernah berisi data, tapi N tanggal terakhir (default 3) 0 baris — **sekali per kejadian**, baru dikirim lagi setelah job sempat berisi data | Sama |
| Uji | Tombol "Kirim email uji" | Alamat yang diisi |

Setelan penerima & saklar disimpan di tabel `settings` (non-rahasia). **Akun SMTP hanya di `.env`**
(`SMTP_HOST/PORT/USER/PASSWORD/FROM/SECURITY`, `SMTP_SECURITY` = `starttls` | `ssl` | `none`) —
tidak ditampilkan di UI dan tidak tersimpan di database. Isi juga `APP_BASE_URL` supaya email
berisi link. Gagal kirim tidak memengaruhi run; semua percobaan tercatat di tabel `notifications`.

### Ekspor Cepat — `webapp/routes_adhoc.py` (halaman **Ekspor Cepat**)

Download `.parquet` dari browser tanpa membuat job dan tanpa menyentuh S3 (skema sama dengan file
datalake, termasuk `_ingested_at`). Maks `ADHOC_MAX_ROWS` baris (default 500.000); transaksi di-rollback.
- **Viewer**: hanya dari **job terdaftar & aktif** + tanggal — tabel sensitif yang tidak dijadikan job
  tidak bisa diambil. Job bertemplate khusus admin (mis. `sp_job` yang menjalankan ETL) hanya admin.
- **Operator/Admin**: juga **tabel bebas** (opsional filter kolom datetime), divalidasi aturan wizard.
- Setiap unduhan tercatat di tabel `adhoc_exports` (siapa, apa, berapa baris).

### Pemeliharaan harian — `pipeline/maintenance.py`

Sekali per hari (WIB) oleh worker: backup `jobs.db` ke `BACKUP_DIR` (default `backups/`, simpan
`BACKUP_KEEP` = 14 terakhir), hapus `RUN_LOG_DIR/*.log` (default `logs/runs/`) & `LOG_DIR/worker_*.log`
(default `logs/`) > `LOG_RETENTION_DAYS` (90) hari, hapus session kedaluwarsa. Log CLI
`logs/exporter_*.log` tidak disentuh. Restore backup:
hentikan web & worker → salin `backups/jobs_YYYYMMDD_HHMMSS.db` menjadi `jobs.db` → jalankan lagi.

**Skema v4**: tabel `schedules`, `settings`, `notifications`, `adhoc_exports`, kolom `runs.schedule_id`.

### Menjalankan permanen di server Windows

Web (`run_web.bat`) dan worker (`run_worker.bat`) harus hidup terus dan otomatis start saat boot.

**Opsi A — NSSM** (service Windows sungguhan, restart otomatis kalau crash; unduh nssm.exe):
```powershell
nssm install ParquetExporterWeb    "D:\path\LoadParquetProcess\run_web.bat"
nssm set     ParquetExporterWeb    AppDirectory "D:\path\LoadParquetProcess"
nssm install ParquetExporterWorker "D:\path\LoadParquetProcess\run_worker.bat"
nssm set     ParquetExporterWorker AppDirectory "D:\path\LoadParquetProcess"
nssm start ParquetExporterWeb; nssm start ParquetExporterWorker
```

**Opsi B — Task Scheduler** (bawaan Windows, jalankan PowerShell sebagai Administrator):
```powershell
schtasks /Create /TN "ParquetExporter\Web"    /TR "\"D:\path\LoadParquetProcess\run_web.bat\""    /SC ONSTART /RU SYSTEM /RL HIGHEST
schtasks /Create /TN "ParquetExporter\Worker" /TR "\"D:\path\LoadParquetProcess\run_worker.bat\"" /SC ONSTART /RU SYSTEM /RL HIGHEST
```
(Task Scheduler tidak me-restart proses yang crash — pantau `/api/health`.)

**HTTPS**: pasang reverse proxy (IIS URL Rewrite + ARR, atau nginx) di depan uvicorn, jalankan web
dengan `WEB_HOST=127.0.0.1` (hanya proxy yang bisa mengakses langsung), set `COOKIE_SECURE=true`
dan `APP_BASE_URL=https://…`. uvicorn otomatis memakai header `X-Forwarded-*` dari 127.0.0.1, jadi IP
klien untuk batas percobaan login tetap benar.

**Monitoring**: `GET /api/health` (tanpa login) → `{"ok": true, "worker_online": true}`; HTTP 503
kalau database tidak bisa dibuka. `worker_online: false` = worker mati → run & jadwal tertahan.

## 11. Deploy dengan Docker (Windows container)

Target: server Docker produksi — **Windows Server 2016, Windows container, process isolation** (cek:
`docker info --format "{{.OSType}}"` → `windows`). Satu image (`Dockerfile.windows`) dipakai **dua
container**: `parquet-exporter-web` (FastAPI + frontend) dan `parquet-exporter-worker`
(`python worker.py`). Keduanya me-mount **folder data yang sama** ke `C:\data` (`jobs.db`, `logs\`,
`output\`, `backups\` — path-nya sudah di-set lewat `ENV` di image). Jangan digabung ke container
aplikasi lain: deploy, dependensi, dan restart-nya jadi saling terikat.

Isi image (detail & alasan di komentar `Dockerfile.windows`):
- Base `mcr.microsoft.com/windows/servercore:ltsc2016` — wajib sama dengan build host (process isolation).
- Tahap 1 build frontend dengan Node (`npm ci` + `npm run build`); tahap 2 Python dari installer
  python.org, Visual C++ Redistributable, ODBC Driver 17, `pip install -r requirements.txt` (versi
  dikunci), lalu `scripts/check_runtime.py` — build GAGAL kalau pyarrow / ODBC Driver tidak siap.
- `.env`, `jobs.py`, `jobs.db`, `logs/`, `output/`, `backups/` tidak masuk image (`.dockerignore`).
  Kredensial diberikan saat container dijalankan (`--env-file`).

### Deploy pertama

Contoh folder di server: kode di `D:\APLIKASI\parquet-exporter`, data di `D:\APLIKASI\parquet-exporter-data`
(di LUAR folder kode, supaya update kode tidak menyentuh data). Port host `8084` (8000/8082/8083
sudah dipakai container lain di server itu).

1. **Kode** ke `D:\APLIKASI\parquet-exporter` (git clone/pull, atau salin folder tanpa `.env`/`jobs.db`/`logs`).
2. **`.env` server** di `D:\APLIKASI\parquet-exporter\.env` (salin dari `.env.example`). Aturan
   `--env-file`: tanpa tanda kutip, tanpa komentar di belakang nilai. **Jangan** isi `JOBS_DB`,
   `LOG_DIR`, `RUN_LOG_DIR`, `OUTPUT_DIR`, `BACKUP_DIR` (sudah diarahkan ke `C:\data` oleh image) —
   kalau diisi path host (`D:\...`), container mencari file yang tidak ada. Isi
   `APP_BASE_URL=http://<server>:8084`.
3. **Data awal** — pindahkan `jobs.db` lama supaya user, job hasil edit UI, kunci S3, dan riwayat run
   ikut. Di mesin lama, hentikan web & worker, lalu buat salinan yang konsisten (API backup SQLite,
   ikut menyertakan isi file `-wal`; menyalin `jobs.db` saja bisa kehilangan data terbaru):
   ```powershell
   python -c "import sqlite3; s = sqlite3.connect('jobs.db'); d = sqlite3.connect('jobs_pindah.db'); s.backup(d); d.close(); s.close()"
   ```
   Salin `jobs_pindah.db` ke `D:\APLIKASI\parquet-exporter-data\jobs.db`. (Environment baru tanpa
   data lama: `docker run` sekali dengan `python migrate_jobs.py` + `jobs.py` — lihat bagian 2.)
4. **Build image** (dari `D:\APLIKASI\parquet-exporter`):
   ```powershell
   docker build -f Dockerfile.windows -t parquet-exporter .
   ```
5. **Cek jaringan dari dalam container** (DNS + TCP ke SQL Server, SSO, SMTP, S3; tanpa login):
   ```powershell
   docker run --rm --env-file .env parquet-exporter python scripts\check_network.py
   ```
   DNS internal di jaringan NAT container server ini bisa gagal (terbukti untuk portal SSO di image lain;
   `--add-host` tidak berlaku di Windows container). Untuk tiap nama yang gagal, ambil IP-nya dari
   **host server Docker** (`Resolve-DnsName <nama>` di server, bukan dari PC lain — hasilnya bisa beda), lalu
   build ulang (cepat, hanya lapisan terakhir yang diulang) dan cek lagi:
   ```powershell
   docker build -f Dockerfile.windows -t parquet-exporter --build-arg HOSTS_ENTRIES="<ip> <nama>;<ip> <nama>" .
   ```
   IP sengaja tidak ditulis di file mana pun yang masuk Git. Simpan perintah build-nya di catatan server.
6. **Jalankan** web & worker (worker **satu instance saja**):
   ```powershell
   $data = "D:\APLIKASI\parquet-exporter-data"
   docker run -d --name parquet-exporter-web --restart unless-stopped --env-file .env -v "${data}:C:\data" -p 8084:8000 parquet-exporter
   docker run -d --name parquet-exporter-worker --restart unless-stopped --env-file .env -v "${data}:C:\data" parquet-exporter python worker.py
   ```
7. **Verifikasi**:
   - `docker logs parquet-exporter-worker` → `Worker ... siap`; `docker logs parquet-exporter-web` → `Uvicorn running`.
   - Dari PC lain: `http://<server>:8084/api/health` → `{"ok": true, "worker_online": true}` — sekaligus
     bukti kedua container berbagi `jobs.db` (heartbeat ditulis worker, dibaca web). Di Windows Server
     2016, port container biasanya tidak bisa dibuka lewat `localhost` dari server itu sendiri — uji
     dari PC lain.
   - Jalankan satu run uji dari UI dengan toggle **Upload ke S3 dimatikan** (`skip_s3`), cek status & log-nya.
   - Buka port `8084` di Windows Firewall / Security Group kalau diakses dari jaringan lain.
8. **Ekspor dobel**: kalau `run_exporter.bat` masih terjadwal di Task Scheduler mesin lain, matikan
   dulu sebelum mengaktifkan jadwal di aplikasi untuk job yang sama (bagian 10).

### Update versi

```powershell
cd D:\APLIKASI\parquet-exporter; git pull
docker build -f Dockerfile.windows -t parquet-exporter --build-arg HOSTS_ENTRIES="..." .   # sama seperti build terakhir
docker rm -f parquet-exporter-web; docker rm -f parquet-exporter-worker
# lalu ulangi kedua `docker run` di langkah 6
```
Hentikan worker saat **tidak ada run yang berjalan** (halaman Riwayat Run) — run yang terputus
ditandai `interrupted` dan bisa diulang. Skema `jobs.db` dinaikkan otomatis saat container start.

### Operasional

```powershell
docker exec parquet-exporter-worker python exporter.py --list                          # CLI di dalam container
docker exec parquet-exporter-worker python manage.py add-user <username.sso> --role admin
docker logs --tail 100 parquet-exporter-worker
```
Log run, output lokal, dan backup harian `jobs.db` ada di folder data host
(`D:\APLIKASI\parquet-exporter-data\logs`, `...\output`, `...\backups`). Restore backup: hentikan kedua
container → salin `backups\jobs_YYYYMMDD_HHMMSS.db` menjadi `jobs.db` → jalankan lagi.
