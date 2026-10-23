# SQL Server → Parquet → S3

Dokumen ini menjelaskan cara kerja `exporter.py` beserta file pendukungnya. Tujuannya:
AI yang membaca file ini bisa langsung paham alur, batasan, dan aturan modifikasi
tanpa perlu membaca ulang seluruh kode.

Referensi baris ditulis sebagai `file.py:NN` dan mengacu pada kondisi kode saat
dokumen ini dibuat.

---

## 1. Apa yang dilakukan pipeline ini

SQL Server 2012 tidak punya dukungan Parquet native. Pipeline ini menjembatani:

```
SQL Server 2012                exporter.py                     AWS S3
┌──────────────────┐      ┌────────────────────┐      ┌──────────────────────┐
│ EXEC dbo.AI_WM_  │      │ pyodbc fetchmany   │      │ datalake/gold/awl/   │
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

| File | Peran | Kapan diedit |
|---|---|---|
| `jobs.db` | **Daftar ekspor** (SQLite, gitignored). Satu baris tabel `jobs` = satu dataset: nama + template + argumen JSON + aktif/nonaktif + catatan. **Inilah yang dibaca `exporter.py`.** | **Lewat Web UI** (halaman Daftar Job → Tambah/Edit) sejak Fase 2 — lihat bagian 9 |
| `jobs.py` | Format lama daftar job (gitignored). Sejak Fase 2 hanya untuk **bootstrap** environment baru (`migrate_jobs.py`) & arsip. | Tidak lagi — `migrate_jobs.py --replace` ditolak kalau sudah ada edit lewat UI |
| `job_validation.py` | Aturan validasi job dari Web UI: identifier dicek ke SQL Server, keunikan nama/output/folder S3, template khusus admin, kunci path S3. | Saat aturan berubah / template baru |
| `job_templates.py` | **Template job** (pindahan helper `jobs.py`): `bronze_daily*`, `bronze_master`, `bronze_arcsde_view`, `sp_job`, `custom_sql` + penyusun S3 key `s3_daily`/`s3_weekly`/`s3_master`. Generik — masuk Git. | Saat butuh pola query baru |
| `job_store.py` | Akses `jobs.db`: skema tabel, `load_job_rows()`, `build_jobs(target_date, rows)`, `default_target_date()`. | Saat struktur tabel berubah |
| `migrate_jobs.py` | Migrasi `jobs.py` → `jobs.db` + **parity check** (lihat di bawah). | Saat menambah template baru (`HELPER_TO_TEMPLATE`) |
| `exporter.py` | **Mesin eksekusi.** Argumen CLI, koneksi, deteksi schema, batching, tulis parquet, upload S3. | Jarang — hanya kalau mekanismenya berubah |
| `job.py` | `@dataclass Job` — definisi struktur satu job (`job.py:10-23`). | Hanya kalau menambah field baru ke Job |
| `config.py` | Connection string, `BATCH_SIZE`, `COMPRESSION`, `OUTPUT_DIR`, `JOBS_DB`, `S3_PREFIX`, kredensial AWS, setelan Web UI (`SSO_BASE_URL`, `SESSION_HOURS`, `COOKIE_SECURE`). Semua rahasia dibaca dari `.env` via `python-dotenv`. | Saat setelan global berubah |
| `.env` | Kredensial nyata (tidak masuk Git). Contohnya di `.env.example`. | — |
| `webapp/`, `worker.py`, `run_store.py`, `manage.py`, `frontend/` | **Web UI** (Fase 1) — lihat bagian 8. | — |

### Daftar job: `jobs.py` → `jobs.db`

```
jobs.py ──(migrate_jobs.py: bedah AST + parity check)──▶ jobs.db ──(job_store.build_jobs)──▶ Job ──▶ exporter.py
```

- Kolom `template` = kunci `TEMPLATES` di `job_templates.py`; `args` = argumen template (JSON).
  Semua nilai yang menentukan query/path S3 (db, layer, module, dataset, nama file, nama SP,
  linked server) disimpan **eksplisit per job** — default helper lama ikut ditulis saat
  migrasi, jadi mengubah default di template tidak diam-diam memindahkan path S3 job lama.
- Satu-satunya setelan global yang ikut menentukan path S3: `S3_PREFIX` (`config.py`, dari `.env`).
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

Struktur `Job` (`job.py`):

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

### `main()` — `exporter.py:241`

1. **Parse argumen** (`:243-257`).
2. **Baca daftar job** dari `jobs.db` SEKALI di awal run (`load_job_rows`) — semua tanggal di
   mode range memakai snapshot yang sama. Database belum ada → error + exit `1`.
   **`--list`**: cetak semua job (yang nonaktif ditandai `[nonaktif]`) lalu keluar. Job
   dibangun untuk tanggal default, jadi `--list` tidak memperhitungkan `--date`.
3. **Validasi tanggal**: `--date` dan `--start-date/--end-date` tidak
   boleh dipakai bersamaan; `--start-date` dan `--end-date` harus berpasangan;
   `end >= start`. Tanpa opsi tanggal apa pun → `default_target_date()` (`job_store.py`):
   kemarin menurut kalender WIB (dikunci `Asia/Jakarta`, bukan timezone OS).
4. **Bangun `date_list`** (`:287`). `is_range = len(date_list) > 1`.
5. **Validasi `--only`** (`:291-299`): nama tak dikenal hanya diperingatkan
   (warning) dan diabaikan; error hanya kalau **tidak ada satu pun** nama yang cocok.
6. **Siapkan klien S3** (`:303-313`): dilewati kalau `--skip-s3`, atau kalau
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` belum lengkap
   (hanya warning, proses tetap lanjut tanpa upload).
7. **Satu koneksi pyodbc** dibuka untuk **seluruh** run (`:324`) — semua job, semua tanggal.
8. **Loop tanggal → loop job** (`:325-347`):
   - `build_jobs(date_str, job_rows)` dipanggil ulang **per tanggal** (hanya job aktif),
     sehingga seluruh S3 key dan `params` ikut menyesuaikan tanggal itu. `--only` hanya
     bisa memilih job aktif — job nonaktif tidak ikut walau cocok pola wildcard.
   - Job master (`needs_date=False`) di mode range dilewati kalau sudah pernah jalan,
     dilacak lewat `date_independent_done` (`:333`).
   - `date_suffix` diisi **hanya** kalau `is_range and job.needs_date` (`:335`) — dipakai
     agar file lokal per tanggal tidak saling menimpa.
   - Kegagalan satu job ditangkap, dicatat, lalu **lanjut ke job berikutnya**, kecuali
     `--stop-on-error`.
9. **Exit code** (`:352-353`): `0` semua sukses, `2` ada job gagal, `1` error argumen
   atau koneksi.

### `run_job()` — `exporter.py:155`

1. **Tentukan tujuan tulis** (`:160-175`):
   - `write_to_s3` = job punya `s3_key` **dan** klien S3 tersedia.
   - `use_local_file` = `not (skip_local and write_to_s3)`. Artinya `--skip-local`
     hanya efektif kalau job memang punya tujuan S3; kalau tidak, file **tetap**
     disimpan lokal supaya hasil query tidak hilang percuma.
   - Kalau tidak menulis lokal, sink-nya `io.BytesIO()` — parquet dibentuk di memori
     lalu langsung `upload_fileobj`.
2. **Eksekusi query** (`:179-184`): `params` dikirim terpisah ke `cursor.execute()`,
   jadi aman dari SQL injection.
3. **Lompati resultset non-SELECT** (`:187`, implementasi di `:142`): SP menjalankan
   `EXEC sp_ETL_LoadSilver…` / `sp_ETL_LoadGold…` sebelum SELECT akhir, yang
   menghasilkan resultset tanpa kolom. Loop `cursor.nextset()` maju sampai
   `cursor.description` tidak `None`. Kalau habis tanpa menemukan resultset berkolom
   → `return 0`, **tanpa membuat file apa pun**.
4. **Bangun schema** (`:192`, implementasi `build_schema` di `:98`), lalu dicatat ke log.
5. **Loop tulis** (`:199-208`):
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
6. **Upload S3** (`:225-236`): hanya kalau `total > 0`. `job.s3_key(first_row)` dipanggil
   di sini — inilah kenapa penyusun key bisa membaca isi data (dipakai oleh `s3_weekly`).

---

## 4. Mekanisme kunci

### Deteksi schema otomatis — `exporter.py:65-105`

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
seperti yang dikembalikan query (`build_schema` di `:103` mencocokkan `overrides.get(col_name)`).

### Kolom audit `_ingested_at` — `exporter.py:run_job()`

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

Default `BATCH_SIZE = 100_000` (`config.py:35`). Angka ini menentukan dua hal sekaligus:
puncak pemakaian memori (`batch × lebar baris`) dan ukuran row group parquet.
Turunkan kalau memori terbatas; naikkan untuk data besar agar row group lebih efisien
dibaca Athena/Spark. Hindari batch sangat kecil untuk data besar — hasilnya banyak
row group kecil yang justru memperlambat pembacaan.

### `first_row` dan S3 key dinamis

`s3_key` adalah **callable** yang menerima dict `{nama_kolom: nilai}` dari **baris
pertama batch pertama**. Ini dipakai `s3_weekly` di `job_templates.py` untuk mengambil
`week_year` / `week_month` / `week_of_month` langsung dari hasil query — batas minggu
ditentukan tabel `dbo.T_PZO_Week_Temp` di SQL Server, jadi tidak boleh dihitung ulang
di Python. Helper lain mengabaikan argumen ini (`lambda _first_row: key`).

### Mode range

```powershell
python exporter.py --start-date 2026-07-01 --end-date 2026-07-15
```

- Job harian dijalankan **sekali per tanggal**; file lokalnya diberi sufiks tanggal
  (`gold_awl_readings_20260701.parquet`) lewat `resolve_output()` (`exporter.py:127`).
- Job master dijalankan **sekali saja** untuk seluruh range.

---

## 5. Layout S3

Konvensi ditentukan oleh penyusun S3 key di `job_templates.py` (`s3_daily`/`s3_weekly`/
`s3_master`). `S3_PREFIX` global di `config.py` (dari `.env`, default `""`); `layer`, `module`,
`dataset`, dan nama file disimpan per job di `jobs.db` (nama file default
`DEFAULT_PART_FILE = "part-00001.parquet"`).

Bucket produksi: `s3://<nama-bucket-s3-anda>/` (isi lewat `S3_BUCKET_NAME` di `.env`, lihat `config.py`). Path lengkap punya **segmen `source=`
opsional** di antara `layer` dan `dataset`, mengikuti taksonomi sistem (mis. `ARS`,
`PZO`, `WLR`, `HMS`, `MCS`). Berbeda dari draf sebelumnya, `source` dan `dataset`
DITULIS key=value juga (bukan folder polos) supaya konsisten hive-style dengan
segmen tanggal:

```
datalake/bronze/source=ars/dataset=hk_transactions/recorded_year=2026/recorded_month=07/recorded_day=20/part-00001.parquet
└──┬───┘ └──┬──┘ └───┬────┘ └───────┬───────────┘ └──────────────────┬──────────────────────────────┘ └──────┬──────┘
prefix    layer    source        dataset                    partisi Hive-style                          nama file
```

Kalau `module=None` (default), segmen `source=` dilewati begitu saja — hasilnya
`{layer}/dataset={dataset}/...`. Ini dipakai untuk job yang belum dipetakan ke modul
manapun (lihat tabel pemetaan di bawah).

Lima keputusan desain yang **jangan diubah tanpa alasan kuat**:

1. **Format `kunci=nilai` pada nama folder.** Inilah yang membuat Athena/Spark/Glue
   crawler otomatis mengenali `recorded_year` dkk sebagai kolom partisi, sehingga
   `WHERE recorded_year=2026 AND recorded_month=07` hanya memindai folder relevan
   (partition pruning).

2. **Nilai partisi diambil dari `target_date`, bukan jam run.** Konsekuensinya
   re-run/backfill tanggal yang sama menimpa partisi yang sama → pipeline idempoten.

3. **Nama file konstan (default `part-00001.parquet`).** Athena/Spark membaca **semua**
   file dalam satu folder partisi lalu menggabungkannya. Karena tiap job mengembalikan
   data **penuh** untuk tanggalnya (bukan delta), nama file yang auto-increment atau
   ber-timestamp antar-run akan membuat data **terhitung dobel**. Nama konstan =
   overwrite = benar. `file_name` boleh di-custom per job (parameter opsional di
   `s3_daily`/`s3_weekly`/`s3_master`), TAPI harus tetap konstan antar-run untuk
   partisi yang sama — bukan untuk auto-increment.

4. **Master diberi awalan `master_`.** Tanpa itu, transaksional `manual_wl_tmat` dan
   master `manual_wl_tmat` jatuh ke folder yang sama dengan skema partisi berbeda
   (`recorded_*` vs `snapshot_date`) — Glue crawler akan gagal menyimpulkan skema.
   Aturan ini tetap berlaku di dalam segmen `module` yang sama (mis. `PZO/manual_wl_tmat`
   vs `PZO/master_manual_wl_tmat`).

5. **Segmen `source=` letaknya SELALU tepat di bawah `layer`, di atas `dataset=`.**
   Konsisten di bronze/silver/gold supaya satu sistem (mis. ARS) gampang ditelusuri
   lintas layer. Job yang belum dipetakan modulnya (lihat tabel di atas) sengaja
   dibiarkan `module=None` daripada ditebak — salah tebak modul di path S3 produksi
   sulit dideteksi (silent, sama seperti tabrakan `s3_key` biasa).

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
   Jangan sentuh `exporter.py`. Pola query yang belum ada = template baru di `job_templates.py`
   + spesifikasinya di `job_validation.TEMPLATE_SPECS` (role minimal) + aturan validasinya +
   `TEMPLATE_FIELDS` di `frontend/src/lib/jobs.js`.
2. **Placeholder parameter pakai `?`, bukan `@param`.** pyodbc memakai gaya qmark;
   `params` harus berurutan sesuai posisi `?`.
3. **`SET NOCOUNT ON;` wajib di awal query SP**, supaya pesan "N rows affected" tidak
   muncul sebagai resultset pengganggu.
4. **`name`, `output`, dan hasil `s3_key` harus unik antar job.** Dua job dengan
   `output` sama akan saling menimpa file lokal; `s3_key` sama akan saling menimpa
   objek S3 — keduanya senyap, tanpa error.
5. **Jangan membuat `PART_FILE` dinamis** (timestamp, UUID, atau nomor berurut) selama
   tiap job masih mengembalikan data penuh per tanggal. Lihat poin 3 di bagian 5.
6. **Kredensial hanya dari environment/`.env`.** Jangan pernah hard-code di `config.py`.
7. **`overrides`** ditulis sebagai objek `pa.DataType` di `jobs.py` (mis. `pa.decimal128(18, 4)`)
   dan tersimpan sebagai teks `str(tipe)` di `jobs.db`; key-nya nama kolom persis.
8. Kalau menambah field ke `Job`, beri **default** di `job.py` agar job lama tidak rusak.
9. **`job_templates.py` masuk Git — jangan tulis nilai spesifik environment di sana** (nama
   database/SP, IP linked server, nama tabel); jadikan argumen template, nilainya tersimpan
   per job di `jobs.db`. Whitespace string query di template sengaja sama persis dengan
   helper lama — mengubahnya membuat parity check gagal.

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
| `run_store.py` | Antrian & riwayat run di `jobs.db`. Klaim run pakai `BEGIN IMMEDIATE`; dua run yang memakai job yang sama tidak pernah jalan bersamaan |
| `worker.py` | Proses terpisah: ambil run `queued`, eksekusi item satu per satu lewat `exporter.run_job()`, heartbeat tiap 15 detik. Run yang heartbeat-nya basi > 90 detik → `interrupted` |
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

### Jadwal — `scheduler.py` (halaman **Jadwal**)

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

### Notifikasi email — `notify.py` (halaman **Pengaturan**, admin)

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

### Pemeliharaan harian — `maintenance.py`

Sekali per hari (WIB) oleh worker: backup `jobs.db` ke `BACKUP_DIR` (default `backups/`, simpan
`BACKUP_KEEP` = 14 terakhir), hapus `logs/runs/*.log` & `logs/worker_*.log` > `LOG_RETENTION_DAYS`
(90) hari, hapus session kedaluwarsa. Log CLI `logs/exporter_*.log` tidak disentuh. Restore backup:
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
