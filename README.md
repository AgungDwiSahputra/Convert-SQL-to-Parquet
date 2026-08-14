# Panduan AI — Pipeline SQL Server → Parquet → S3

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
| `jobs.py` | **Daftar ekspor.** Satu entri = satu dataset. Berisi juga helper penyusun S3 key. | Setiap kali menambah/mengubah ekspor — **ini satu-satunya file yang normalnya perlu diedit** |
| `exporter.py` | **Mesin eksekusi.** Argumen CLI, koneksi, deteksi schema, batching, tulis parquet, upload S3. | Jarang — hanya kalau mekanismenya berubah |
| `job.py` | `@dataclass Job` — definisi struktur satu job (`job.py:10-23`). | Hanya kalau menambah field baru ke Job |
| `config.py` | Connection string, `BATCH_SIZE`, `COMPRESSION`, `OUTPUT_DIR`, kredensial AWS. Semua rahasia dibaca dari `.env` via `python-dotenv`. | Saat setelan global berubah |
| `.env` | Kredensial nyata (tidak masuk Git). Contohnya di `.env.example`. | — |

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
2. **`--list`** (`:259`): cetak daftar job dari konstanta `JOBS` lalu keluar. Perlu
   diketahui: `JOBS` dibangun dari `TARGET_DATE` default, jadi `--list` tidak
   memperhitungkan `--date`.
3. **Validasi tanggal** (`:265-285`): `--date` dan `--start-date/--end-date` tidak
   boleh dipakai bersamaan; `--start-date` dan `--end-date` harus berpasangan;
   `end >= start`. Tanpa opsi tanggal apa pun → pakai `TARGET_DATE` dari `jobs.py:93`.
4. **Bangun `date_list`** (`:287`). `is_range = len(date_list) > 1`.
5. **Validasi `--only`** (`:291-299`): nama tak dikenal hanya diperingatkan
   (warning) dan diabaikan; error hanya kalau **tidak ada satu pun** nama yang cocok.
6. **Siapkan klien S3** (`:303-313`): dilewati kalau `--skip-s3`, atau kalau
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` belum lengkap
   (hanya warning, proses tetap lanjut tanpa upload).
7. **Satu koneksi pyodbc** dibuka untuk **seluruh** run (`:324`) — semua job, semua tanggal.
8. **Loop tanggal → loop job** (`:325-347`):
   - `build_jobs(date_str)` dipanggil ulang **per tanggal**, sehingga seluruh S3 key
     dan `params` ikut menyesuaikan tanggal itu.
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
pertama batch pertama**. Ini dipakai `s3_weekly` di `jobs.py:132` untuk mengambil
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

Konvensi ditentukan sepenuhnya oleh helper di `jobs.py`. Konstanta pengatur:
`S3_PREFIX` (`:105`), `LAYER` (`:100`), `PART_FILE` (`:109`, sekarang `"part-00001.parquet"`).

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

⚠️ **Implikasi Glue penting (keputusan disengaja, bukan default aman):** karena
`source=`/`dataset=` berbentuk key=value, Glue Crawler yang di-run dari root
`{layer}/` akan membacanya sebagai **kolom partisi satu tabel bersama**, bukan
sekadar penamaan folder. Ini valid HANYA kalau crawl dilakukan **per folder
dataset** (satu tabel per kombinasi source+dataset), bukan crawl langsung dari
root layer — karena skema kolom antar dataset berbeda total (mis. `hk_transactions`
vs `wm_transactions` datang dari tabel SQL Server yang sama sekali berbeda).
Kalau nanti mau benar-benar satu tabel gabungan per layer, skema semua dataset
di layer itu harus diseragamkan dulu (union schema) — belum dilakukan di sini.

| Helper | Dipakai untuk | Pola |
|---|---|---|
| `s3_daily()` `jobs.py:123` | Transaksional harian | `{layer}/source={module}/dataset={dataset}/recorded_year=YYYY/recorded_month=MM/recorded_day=DD/{file_name}` |
| `s3_weekly()` `jobs.py:141` | TMAT (granular mingguan) | `{layer}/source={module}/dataset={dataset}/recorded_year=YYYY/recorded_month=MM/recorded_week=W{n}/{file_name}` |
| `s3_master()` `jobs.py:159` | Master / dimensi | `{layer}/source={module}/dataset=master_{dataset}/snapshot_date=YYYY-MM-DD/{file_name}` |

Ketiga helper menerima parameter `module` (default `None`, ditulis sebagai `source=`)
dan `file_name` (default `PART_FILE`) selain `dataset`/`layer` yang sudah ada sejak awal.

Pemetaan modul saat ini (evidence-based dari penamaan job yang sudah ada, bukan tebakan):

| Modul | Job yang dipetakan | Dasar pemetaan |
|---|---|---|
| `ARS` | `ars` (dataset `transactions`), `bronze_ars_hk` (`hk_transaction`), `bronze_ars_wm` (`wm_transaction`) | Nama SP `sp_ETL_LoadSilver_ARS_Transactions` + nama job `ars` |
| `PZO` | `tmat`, `tmat_pz`, `bronze_tmat_pz` | Job lama sudah bernama `tmat_**pz**` |
| `WLR` | `tmas`, `tmas_wlr`, `bronze_tmas_wlr` | Job lama sudah bernama `tmas_**wlr**` |
| `HMS` | *(belum ada job aktif — job `hms_tpanen` pernah ada, dihapus)* | Diagram: fitur `panen`/`kirim`/`timbangan` |
| `MCS` | *(belum ada job)* | Belum dikonfirmasi isinya |
| *(tanpa modul)* | `awl`, `rs`, `wm`, `dev`, `estate`/`bronze_estate`, `block`/`bronze_block`, `awm`/`bronze_awm`, `ombro`/`bronze_ombro` | Belum ada instruksi modul mana yang cocok — tetap flat `{layer}/{dataset}/...` sampai dikonfirmasi |

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
| *(tanpa opsi)* | Jalan untuk `TARGET_DATE` di `jobs.py:93` |
| `--date YYYY-MM-DD` | Satu tanggal tertentu |
| `--start-date` + `--end-date` | Rentang tanggal, inklusif |
| `--only NAMA…` | Batasi ke job tertentu -- tiap item boleh nama persis ATAU pola wildcard (`*`, `?`, lihat `job_matches()` di `exporter.py`), mis. `--only "bronze_hms_*"` untuk satu grup sekaligus |
| `-b`, `--batch N` | Baris per fetch / per row group (default `100_000`) |
| `-c`, `--compression` | `zstd` (default), `snappy`, `gzip`, `none` |
| `--list` | Cetak daftar job lalu keluar |
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

1. **Tambah ekspor baru = tambah satu entri di `build_jobs()` pada `jobs.py`.** Jangan
   sentuh `exporter.py`.
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
7. **`overrides` memakai objek `pa.DataType`** (mis. `pa.decimal128(18, 4)`), dengan key
   berupa nama kolom persis.
8. Kalau menambah field ke `Job`, beri **default** di `job.py` agar job lama tidak rusak.

---

## 8. Perilaku yang mudah salah paham

| Perilaku | Penjelasan |
|---|---|
| **Query 0 baris → file lokal tetap dibuat** | `ParquetWriter` dibuat sebelum fetch pertama (`exporter.py:199`), jadi hasilnya file parquet valid berisi 0 baris. Upload S3 **dilewati** (`total > 0`), sehingga partisi S3 tidak terbentuk. File lokal ada, S3 tidak — ini normal, bukan bug |
| **Resultset tanpa kolom → tidak ada file sama sekali** | Beda dari kasus di atas: `run_job` `return 0` lebih awal (`:190`) sebelum writer dibuat |
| **`s3_weekly` memakai baris pertama saja** | Kalau satu hasil query berisi lebih dari satu minggu, **seluruh** data masuk ke partisi minggu milik baris pertama. Aman selama SP mengembalikan tepat satu minggu per pemanggilan |
| **Master di mode range → `snapshot_date` = tanggal awal range** | Karena job master hanya jalan sekali untuk seluruh range |
| **`--skip-local` bisa diabaikan** | Kalau job tidak punya tujuan S3, file tetap ditulis lokal dan muncul warning (`:173-175`) |
| **`--list` tidak terpengaruh `--date`** | Memakai konstanta `JOBS` yang dibangun dari `TARGET_DATE` |
| **Nama/pola salah di `--only` hanya warning** | Error hanya kalau tidak ada satu pun nama/pola yang cocok. `--only` mendukung wildcard (`*`, `?`) lewat `job_matches()` — pola yang salah ketik (mis. `bronze_hsm_*`, typo) tidak akan cocok apa pun tapi cuma memicu warning, bukan error, kalau pola lain di daftar tetap cocok |
| **Timestamp tanpa timezone** | `timestamp("us")` naive, diasumsikan WIB. Tidak ada konversi ke UTC — konsumen hilir harus tahu ini. **Kecuali** `_ingested_at` — itu satu-satunya kolom yang tz-aware UTC, sengaja beda karena itu metadata pipeline bukan data sumber |
| **`_ingested_at` = load terakhir, bukan histori** | Re-run job yang sama untuk partisi yang sama menimpa `_ingested_at` lama dengan yang baru (konsisten dengan overwrite-per-partition). Kalau butuh riwayat semua percobaan, itu belum ada — perlu run-log terpisah |
| **Exit code `2`** | Berarti sebagian job gagal, bukan error fatal. `1` = error argumen/koneksi |
| **Satu koneksi untuk seluruh run** | Run rentang panjang menahan satu koneksi SQL Server cukup lama |

---

## 9. Verifikasi tanpa koneksi database

`jobs.py` tidak mengimpor `config.py`, jadi seluruh S3 key bisa diperiksa tanpa
menyentuh SQL Server maupun AWS. Berguna setelah mengubah helper S3:

```python
from jobs import build_jobs

dummy = {"week_year": 2026, "week_month": 7, "week_of_month": 1}   # untuk job weekly
seen = {}
for j in build_jobs("2026-07-20"):
    key = j.s3_key(dummy) if j.s3_key else "(tidak diupload)"
    print(f"{j.name:<16} {key}")
    if j.s3_key:
        seen.setdefault(key, []).append(j.name)

print("bertabrakan:", {k: v for k, v in seen.items() if len(v) > 1} or "tidak ada")
```

Selalu jalankan pemeriksaan tabrakan ini setelah menambah job atau mengubah helper —
tabrakan S3 key tidak memunculkan error apa pun saat runtime, datanya hanya hilang
tertimpa.

---

## 10. Catatan status & keputusan terbuka

- **Dispatcher `dbo.AI_WM_Assistant`** — `@function` 1, 2, 4, 5, 6, 13 membutuhkan
  `@TargetDate`; `@function` 3, 7–12 tidak meneruskan tanggal ke SELECT akhir sehingga
  cukup dikirim `@function` saja (`needs_date=False`).
- **Job `bronze_*` menduplikasi job master `gold`.** `bronze_estate`/`estate` (function 7),
  `bronze_block`/`block` (8), `bronze_tmat_pz`/`tmat_pz` (9), `bronze_tmas_wlr`/`tmas_wlr` (10),
  `bronze_awm`/`awm` (11), `bronze_ombro`/`ombro` (12) memanggil `@function` yang **sama
  persis**. Akibatnya SP dieksekusi dua kali dan isi `bronze/` identik dengan `gold/`.
  Layer bronze idealnya berisi data **mentah sebelum transformasi**, yang berarti perlu
  `@function` berbeda atau SELECT langsung ke tabel sumber. Perlu keputusan.
- **Komentar `TODO konfirmasi` di `jobs.py:174-178` sudah kedaluwarsa** — mengacu pada
  `function=13` dan tabrakan output yang sudah tidak ada lagi.
- **File lokal di `output/` masih rata (flat)**, tidak mengikuti struktur partisi S3.
  Diatur `resolve_output()` (`exporter.py:127`).
- **Path S3 lama** (`parquet_format/transaksional/…`, `parquet_format/master/…`) beserta
  konvensi nama proses C# existing sudah **ditinggalkan**. Objek lama tidak terhapus
  sendiri; perlu backfill atau pemberitahuan ke konsumen hilir.
- **`import pyarrow as pa` di `jobs.py:86` saat ini tidak terpakai** — disiapkan untuk
  `overrides`, yang belum dipakai job mana pun.
- **`schema.yml` belum ada dan sifatnya opsional.** Schema sudah tertanam di footer file
  parquet, jadi konsumen tidak membutuhkannya. Baru relevan kalau nanti dibutuhkan
  deteksi schema drift, kontrak antar-tim, atau generator DDL Glue/Athena.
