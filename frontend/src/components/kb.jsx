import { AlertTriangle, CheckCircle2, CircleDashed, Clock, Loader2 } from 'lucide-react'
import { cx } from './ui'
import { fmtDateTime, fmtNumber, fmtRelative } from '../lib/format'

// =====================================================================================================
// Bagian SEDERHANA (semua peran) -- data dari webapp/simple_view.py. Kosakata status sengaja kecil & sama di semua halaman.
// =====================================================================================================

// Status Knowledge Base (simple_view.kb_status)
const KB = {
  ready: ['Siap', 'bg-brand-50 text-brand-700', 'bg-brand-500'],
  updating: ['Sedang diperbarui', 'bg-blue-50 text-blue-700', 'bg-blue-500'],
  problem: ['Ada kendala', 'bg-amber-50 text-amber-800', 'bg-amber-500'],
  none: ['Belum pernah diperbarui', 'bg-gray-100 text-gray-600', 'bg-gray-400'],
  off: ['Tidak dipantau', 'bg-gray-100 text-gray-500', 'bg-gray-300'],
  unknown: ['Status belum tersedia', 'bg-gray-100 text-gray-500', 'bg-gray-300'],
}

export function SimpleKbBadge({ status }) {
  const [label, chip, dot] = KB[status] || KB.unknown
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium', chip)}>
      <span className={cx('h-1.5 w-1.5 rounded-full', dot, status === 'updating' && 'animate-pulse')} aria-hidden />
      {label}
    </span>
  )
}

// Hasil satu pembaruan (simple_view.kb_ingestions)
const RESULT = {
  done: ['Berhasil', 'bg-brand-50 text-brand-700'],
  failed: ['Gagal', 'bg-red-50 text-red-700'],
  stopped: ['Dihentikan', 'bg-gray-100 text-gray-600'],
  running: ['Berjalan', 'bg-blue-50 text-blue-700'],
}

export function SimpleResultBadge({ status }) {
  const [label, chip] = RESULT[status] || RESULT.running
  return <span className={cx('inline-flex whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', chip)}>{label}</span>
}

/** Catatan kendala satu Knowledge Base dalam bahasa sehari-hari (daftar & detail). */
export function KbNotes({ t }) {
  return (
    <>
      {t.reason && <p className="text-xs text-amber-800">{t.reason}</p>}
      {t.last_attempt_failed && t.updated_at && (
        <p className="text-xs text-amber-800">Pembaruan terakhir belum berhasil — isi yang dipakai masih versi {fmtDateTime(t.updated_at)}.</p>
      )}
      {t.failed_documents > 0 && <p className="text-xs text-amber-800">{fmtNumber(t.failed_documents)} dokumen belum bisa diproses.</p>}
    </>
  )
}

/** "4 baru · 2 diubah · 1 dihapus"; tanpa perubahan -> teks netral. */
export function changeText(c) {
  if (!c) return null
  const parts = [c.added && `${c.added} baru`, c.modified && `${c.modified} diubah`, c.deleted && `${c.deleted} dihapus`].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Tanpa perubahan isi'
}

// Langkah alur (simple_view.kb_steps)
const STEP = {
  done: [CheckCircle2, 'text-brand-600', 'Selesai'],
  running: [Loader2, 'text-blue-600 animate-spin', 'Sedang berjalan'],
  waiting: [Clock, 'text-gray-400', 'Menunggu'],
  problem: [AlertTriangle, 'text-amber-600', 'Ada kendala'],
  unknown: [CircleDashed, 'text-gray-300', 'Belum tersedia'],
}

export function StepList({ steps }) {
  return (
    <ol className="grid gap-3 sm:grid-cols-3">
      {steps.map((s, i) => {
        const [Icon, color, label] = STEP[s.status] || STEP.unknown
        return (
          <li key={s.key} className="flex items-start gap-3 rounded-lg border border-gray-100 bg-gray-50/60 p-3">
            <Icon className={cx('mt-0.5 h-5 w-5 shrink-0', color)} aria-hidden />
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-800"><span className="text-gray-400">{i + 1}.</span> {s.label}</p>
              <p className="mt-0.5 text-xs text-gray-500">
                {label}
                {s.at && <> · <span title={fmtDateTime(s.at)}>{fmtRelative(s.at)}</span></>}
              </p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// =====================================================================================================
// Bagian TEKNIS (hanya Operator/Admin, di dalam "Detail teknis") -- data dari blok `tech` respons API.
// =====================================================================================================

// Status satu ingestion job Bedrock.
const INGESTION = {
  COMPLETE: ['Selesai', 'bg-brand-50 text-brand-700'],
  FAILED: ['Gagal', 'bg-red-50 text-red-700'],
  IN_PROGRESS: ['Berjalan', 'bg-blue-50 text-blue-700'],
  STARTING: ['Memulai', 'bg-blue-50 text-blue-700'],
  STOPPING: ['Menghentikan', 'bg-gray-100 text-gray-600'],
  STOPPED: ['Dihentikan', 'bg-gray-100 text-gray-600'],
}

export function IngestionBadge({ status }) {
  const [label, classes] = INGESTION[status] || [status, 'bg-gray-100 text-gray-600']
  return (
    <span className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', classes)}>{label}</span>
  )
}

// Asal pemicu ingestion (pipeline/kb_pipeline.attribute). Glue = ada laporan run Glue yang selesai ≤ 30 dtk sesudah ingestion
// mulai (KESIMPULAN dari waktu, bukan catatan Bedrock). Terjadwal = laporan selesai di jendela jadwal; selain itu run Glue di luar jadwal.
export function originInfo(i) {
  if (i.origin === 'app') return { label: 'Aplikasi', hint: 'Dimulai dari aplikasi ini.' }
  if (i.origin === 'glue') {
    return i.glue_scheduled
      ? { label: 'Glue · terjadwal', hint: 'Dimulai job Glue dokumen pada run terjadwal. Disimpulkan dari waktu: laporan run Glue selesai ≤ 30 detik sesudah ingestion mulai.' }
      : { label: 'Glue · di luar jadwal', hint: 'Dimulai job Glue dokumen pada run di luar jadwal (job dijalankan di luar jam jadwal; ingestion tetap dimulai Glue). Disimpulkan dari waktu laporan.' }
  }
  return { label: 'Di luar aplikasi', hint: 'Tidak ada laporan Glue di dekat waktu ini — umumnya console AWS (manual) atau pemuatan awal.' }
}

// Tahap jalur dokumen (pipeline/kb_pipeline): status Glue & KB.
const GLUE_STAGE = {
  running: ['Glue berproses', 'bg-blue-50 text-blue-700'],
  failed: ['Glue gagal', 'bg-red-50 text-red-700'],
  ok: ['Glue segar', 'bg-brand-50 text-brand-700'],
  waiting: ['Glue menunggu', 'bg-gray-100 text-gray-600'],
  late: ['Glue terlambat', 'bg-red-50 text-red-700'],
  unreadable: ['Glue tak terbaca', 'bg-amber-50 text-amber-700'],
}
const KB_STAGE = {
  ok: ['KB terbarui', 'bg-brand-50 text-brand-700'],
  running: ['KB berjalan', 'bg-blue-50 text-blue-700'],
  waiting: ['KB menunggu', 'bg-gray-100 text-gray-600'],
  pending: ['KB tertunda', 'bg-red-50 text-red-700'],
  failed: ['KB gagal', 'bg-red-50 text-red-700'],
  none: ['KB belum berubah', 'bg-gray-100 text-gray-500'],
  unreadable: ['KB tak terbaca', 'bg-amber-50 text-amber-700'],
}

export function StageBadge({ kind, status }) {
  const [label, classes] = (kind === 'glue' ? GLUE_STAGE : KB_STAGE)[status] || ['–', 'bg-gray-100 text-gray-500']
  return <span className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', classes)}>{label}</span>
}

// Status permintaan proses manual (pipeline/kb_actions.py).
const ACTION_STATUS = {
  queued: ['Menunggu', 'bg-gray-100 text-gray-600'],
  running: ['Diproses', 'bg-blue-50 text-blue-700'],
  started: ['Dimulai', 'bg-brand-50 text-brand-700'],
  rejected: ['Ditolak', 'bg-amber-50 text-amber-700'],
  failed: ['Gagal', 'bg-red-50 text-red-700'],
}

export function ActionStatusBadge({ status }) {
  const [label, classes] = ACTION_STATUS[status] || [status, 'bg-gray-100 text-gray-600']
  return <span className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', classes)}>{label}</span>
}

export const PIPELINE_LABEL = { documents: 'Jalur dokumen (Google Drive → pembersihan → Knowledge Base)', manual: 'Manual / lainnya' }
