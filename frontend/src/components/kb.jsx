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

// Asal pemicu ingestion. 'external' = dimulai di luar aplikasi ini (job Glue dokumen, console AWS, dst.).
export const ORIGIN_LABEL = { app: 'Aplikasi', glue: 'Glue (otomatis)', external: 'Di luar aplikasi' }
export const ORIGIN_HINT = {
  app: 'Dimulai dari aplikasi ini.',
  glue: 'Dimulai otomatis oleh job Glue dokumen.',
  external: 'Dimulai di luar aplikasi ini — umumnya job Glue dokumen atau console AWS.',
}
export const PIPELINE_LABEL = { documents: 'Jalur dokumen (rclone → Glue → KB)', manual: 'Manual / lainnya' }

/** Ringkasan statistik satu ingestion: '5 baru · 2 diubah · 0 dihapus · 1 gagal'. */
export function ingestionStats(i) {
  if (!i) return ''
  const parts = [`${i.new_indexed ?? 0} baru`, `${i.modified_indexed ?? 0} diubah`, `${i.deleted ?? 0} dihapus`]
  parts.push(`${i.failed ?? 0} gagal`)
  return parts.join(' · ')
}
