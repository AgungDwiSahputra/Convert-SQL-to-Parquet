import { cx } from './ui'

// Kesehatan target Knowledge Base (pipeline/kb_sync.health_of): running | ok | warning | failed | stopped | none.
const HEALTH = {
  ok: ['Sehat', 'bg-brand-50 text-brand-700', 'bg-brand-500'],
  running: ['Sedang berjalan', 'bg-blue-50 text-blue-700', 'bg-blue-600'],
  warning: ['Ada dokumen gagal', 'bg-amber-50 text-amber-700', 'bg-amber-500'],
  failed: ['Ingestion gagal', 'bg-red-50 text-red-700', 'bg-red-500'],
  stopped: ['Dihentikan', 'bg-gray-100 text-gray-600', 'bg-gray-400'],
  none: ['Belum ada ingestion', 'bg-gray-100 text-gray-500', 'bg-gray-300'],
}

export function KbHealthBadge({ health }) {
  const [label, classes, dot] = HEALTH[health] || HEALTH.none
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium', classes)}>
      <span className="relative flex h-1.5 w-1.5">
        {health === 'running' && <span className={cx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', dot)} />}
        <span className={cx('relative inline-flex h-1.5 w-1.5 rounded-full', dot)} />
      </span>
      {label}
    </span>
  )
}

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

/** Dua penanda kecil (Glue, KB) untuk target jalur dokumen di daftar. */
export function FlowChips({ flow }) {
  if (!flow?.glue || !flow?.kb) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <StageBadge kind="glue" status={flow.glue.status} />
      <StageBadge kind="kb" status={flow.kb.status} />
    </span>
  )
}

export const PIPELINE_LABEL = { documents: 'Jalur dokumen (rclone → Glue → KB)', manual: 'Manual / lainnya' }

/** Ringkasan statistik satu ingestion: '5 baru · 2 diubah · 0 dihapus · 1 gagal'. */
export function ingestionStats(i) {
  if (!i) return ''
  const parts = [`${i.new_indexed ?? 0} baru`, `${i.modified_indexed ?? 0} diubah`, `${i.deleted ?? 0} dihapus`]
  parts.push(`${i.failed ?? 0} gagal`)
  return parts.join(' · ')
}
