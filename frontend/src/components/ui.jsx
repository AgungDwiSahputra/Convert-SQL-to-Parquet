import { useEffect, useRef } from 'react'
import { AlertTriangle, CircleAlert, Info, Loader2, X } from 'lucide-react'

export const cx = (...c) => c.filter(Boolean).join(' ')

export function Spinner({ className = 'h-4 w-4' }) {
  return <Loader2 className={cx('animate-spin', className)} aria-hidden />
}

const BTN_VARIANT = {
  primary: 'bg-brand-600 text-white shadow-sm shadow-brand-600/25 hover:bg-brand-700 active:bg-brand-800',
  secondary: 'border border-gray-300 bg-white text-gray-700 shadow-sm hover:border-gray-400 hover:bg-gray-50',
  danger: 'bg-red-600 text-white shadow-sm hover:bg-red-700',
  ghost: 'text-gray-600 hover:bg-gray-100 hover:text-gray-800',
  soft: 'bg-brand-50 text-brand-700 hover:bg-brand-100',
}
const BTN_SIZE = {
  sm: 'h-9 px-3 text-sm',
  md: 'h-11 px-4 text-sm sm:h-10',
}

export function Button({ variant = 'primary', size = 'md', icon: Icon, loading = false, className, children, ...props }) {
  return (
    <button
      type="button"
      {...props}
      disabled={loading || props.disabled}
      className={cx(
        'inline-flex shrink-0 items-center justify-center gap-2 rounded-lg font-semibold transition-all duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2',
        'disabled:pointer-events-none disabled:opacity-50',
        BTN_SIZE[size], BTN_VARIANT[variant], className,
      )}
    >
      {loading ? <Spinner /> : Icon && <Icon className="h-4 w-4" aria-hidden />}
      {children}
    </button>
  )
}

export function Card({ className, children }) {
  return <div className={cx('rounded-xl border border-gray-200/80 bg-white shadow-card', className)}>{children}</div>
}

export function CardHeader({ title, description, icon: Icon, action, className }) {
  return (
    <div className={cx('flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-4', className)}>
      <div className="flex min-w-0 items-center gap-3">
        {Icon && <IconChip icon={Icon} size="sm" />}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

/** Ikon dalam kotak berwarna lembut (soft blue default, atau mint). */
export function IconChip({ icon: Icon, tone = 'blue', size = 'md' }) {
  const tones = { blue: 'bg-blue-50 text-blue-600', mint: 'bg-brand-50 text-brand-600', red: 'bg-red-50 text-red-600', amber: 'bg-amber-50 text-amber-600', gray: 'bg-gray-100 text-gray-500' }
  const sizes = { sm: ['h-8 w-8 rounded-lg', 'h-4 w-4'], md: ['h-10 w-10 rounded-xl', 'h-5 w-5'] }
  return (
    <span className={cx('flex shrink-0 items-center justify-center', sizes[size][0], tones[tone])}>
      <Icon className={sizes[size][1]} aria-hidden />
    </span>
  )
}

export function PageHeader({ title, description, actions }) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-800">{title}</h1>
        {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

const ALERT_TONE = {
  error: ['border-red-200 bg-red-50 text-red-800', 'text-red-600', CircleAlert],
  warning: ['border-amber-200 bg-amber-50 text-amber-900', 'text-amber-600', AlertTriangle],
  info: ['border-brand-100 bg-brand-50 text-brand-900', 'text-brand-600', Info],
}

export function Alert({ tone = 'info', title, children, className }) {
  const [classes, iconColor, Icon] = ALERT_TONE[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('flex gap-3 rounded-xl border px-4 py-3 text-sm', classes, className)}>
      <Icon className={cx('mt-0.5 h-4 w-4 shrink-0', iconColor)} aria-hidden />
      <div className="min-w-0 space-y-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="break-words leading-relaxed">{children}</div>}
      </div>
    </div>
  )
}

export function LoadingBlock({ label = 'Memuat…' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
      <Spinner className="h-4 w-4 text-brand-600" /> {label}
    </div>
  )
}

export function EmptyState({ icon: Icon, title, children, action }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      {Icon && <div className="mb-4"><IconChip icon={Icon} tone="mint" /></div>}
      <p className="font-semibold text-gray-800">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-gray-500">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

const STATUS = {
  queued: ['Mengantri', 'bg-gray-100 text-gray-600', 'bg-gray-400'],
  pending: ['Menunggu', 'bg-gray-100 text-gray-500', 'bg-gray-300'],
  running: ['Berjalan', 'bg-blue-50 text-blue-700', 'bg-blue-600'],
  success: ['Sukses', 'bg-brand-50 text-brand-700', 'bg-brand-500'],
  failed: ['Gagal', 'bg-red-50 text-red-700', 'bg-red-500'],
  cancelled: ['Dibatalkan', 'bg-gray-100 text-gray-600', 'bg-gray-400'],
  skipped: ['Dilewati', 'bg-gray-100 text-gray-500', 'bg-gray-300'],
  interrupted: ['Terputus', 'bg-orange-50 text-orange-700', 'bg-orange-500'],
}

export const ACTIVE_STATUSES = ['queued', 'running']
export const FINAL_STATUSES = ['success', 'failed', 'cancelled', 'interrupted']

export function StatusBadge({ status }) {
  const [label, classes, dot] = STATUS[status] || [status, STATUS.pending[1], STATUS.pending[2]]
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium', classes)}>
      <span className="relative flex h-1.5 w-1.5">
        {status === 'running' && <span className={cx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', dot)} />}
        <span className={cx('relative inline-flex h-1.5 w-1.5 rounded-full', dot)} />
      </span>
      {label}
    </span>
  )
}

const BADGE_TONE = {
  gray: 'bg-gray-100 text-gray-600',
  mint: 'bg-brand-50 text-brand-700',
  blue: 'bg-blue-50 text-blue-700',
  amber: 'bg-amber-50 text-amber-700',
}

// Hasil data quality per item run (pipeline/dq.py): ok | warning | blocked | unchecked.
const DQ_STATUS = {
  ok: ['Lolos', 'bg-brand-50 text-brand-700'],
  warning: ['Peringatan', 'bg-amber-50 text-amber-700'],
  blocked: ['Ditahan', 'bg-red-50 text-red-700'],
  unchecked: ['Tidak dicek', 'bg-gray-100 text-gray-500'],
}

export function DqBadge({ status }) {
  const [label, classes] = DQ_STATUS[status] || [status, DQ_STATUS.unchecked[1]]
  return (
    <span className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', classes)}>
      {label}
    </span>
  )
}

// Kesegaran data job (pipeline/freshness.py): fresh | late | unknown.
const FRESHNESS = {
  fresh: ['Segar', 'bg-brand-50 text-brand-700'],
  late: ['Terlambat', 'bg-red-50 text-red-700'],
  unknown: ['Belum ada data', 'bg-gray-100 text-gray-500'],
}

export function FreshnessBadge({ freshness }) {
  if (!freshness) return null
  const [label, classes] = FRESHNESS[freshness.status] || FRESHNESS.unknown
  const title = freshness.latest
    ? `Data terbaru ${freshness.latest} · seharusnya ≥ ${freshness.expected}`
    : `Belum ada tanggal yang diproses · seharusnya ${freshness.expected}`
  return (
    <span title={title} className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', classes)}>
      {label}{freshness.status === 'late' && freshness.lag_days ? ` · ${freshness.lag_days} hari` : ''}
    </span>
  )
}

// Celah data (pipeline/freshness.find_gaps): tanggal/minggu/bulan di tengah riwayat yang belum ter-load.
export function GapBadge({ gaps }) {
  if (!gaps?.count) return null
  return (
    <span
      title={gaps.items.map((g) => g.label).join('\n')}
      className="inline-flex items-center whitespace-nowrap rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700"
    >
      Ada celah · {gaps.count}
    </span>
  )
}

export function Badge({ children, tone = 'gray', className }) {
  return (
    <span className={cx('inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium', BADGE_TONE[tone], className)}>
      {children}
    </span>
  )
}

export function ProgressBar({ value, total, status }) {
  const pct = total ? Math.round((value / total) * 100) : 0
  const color =
    status === 'failed' || status === 'interrupted' ? 'bg-red-500'
      : status === 'cancelled' ? 'bg-gray-400'
        : status === 'running' || status === 'queued' ? 'bg-blue-600'
          : 'bg-brand-500'
  return (
    <div className="flex items-center gap-2.5">
      <div className="h-1.5 w-full min-w-16 overflow-hidden rounded-full bg-gray-100" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={cx('h-full rounded-full transition-all duration-500', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 shrink-0 text-right text-xs tabular-nums text-gray-500">{value}/{total}</span>
    </div>
  )
}

export function Toggle({ checked, onChange, label, description, id }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-gray-800">{label}</label>
        {description && <p className="text-xs text-gray-500">{description}</p>}
      </div>
      <Switch id={id} checked={checked} onChange={onChange} />
    </div>
  )
}

export function Switch({ id, checked, onChange, disabled, label }) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:opacity-50',
        checked ? 'bg-brand-500' : 'bg-gray-300',
      )}
    >
      <span className={cx('inline-block h-5 w-5 rounded-full bg-white shadow transition-transform duration-200', checked ? 'translate-x-5' : 'translate-x-0.5')} />
    </button>
  )
}

export const inputClass =
  'block w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-800 shadow-sm placeholder:text-gray-400 ' +
  'transition-colors focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/15 disabled:bg-gray-50 disabled:text-gray-500 sm:py-2'

export function Field({ label, htmlFor, hint, children }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-gray-700">{label}</label>
      {children}
      {hint && <p className="text-xs text-gray-500">{hint}</p>}
    </div>
  )
}

export function Modal({ open, onClose, title, description, children, footer, size = 'lg' }) {
  const panelRef = useRef(null)
  // onClose lewat ref: efek di bawah hanya jalan saat modal dibuka/ditutup, bukan tiap render
  // (kalau tidak, fokus "dicuri" kembali ke panel setiap kali isi modal berubah).
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => e.key === 'Escape' && onCloseRef.current()
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panelRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open])
  if (!open) return null
  const width = { md: 'sm:max-w-lg', lg: 'sm:max-w-2xl', xl: 'sm:max-w-4xl' }[size]
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        className={cx('relative flex max-h-[92vh] w-full flex-col rounded-t-2xl bg-white shadow-raised ring-1 ring-gray-900/5 focus:outline-none sm:rounded-2xl', width)}
      >
        <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id="modal-title" className="text-lg font-semibold text-gray-800">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-gray-500">{description}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="Tutup" className="-m-1 rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600">
            <X className="h-5 w-5" aria-hidden />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
        {footer && (
          <div className="flex flex-col-reverse gap-2 rounded-b-2xl border-t border-gray-100 bg-gray-50/60 px-5 py-4 sm:flex-row sm:justify-end sm:px-6">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export function Stat({ label, value, sub }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-gray-500">{label}</p>
      <p className="mt-1 truncate text-lg font-semibold tabular-nums text-gray-800">{value}</p>
      {sub && <p className="truncate text-xs text-gray-500">{sub}</p>}
    </div>
  )
}

/** Kartu angka ringkas untuk Beranda. */
export function StatCard({ icon, tone = 'blue', label, value, sub, onClick, children }) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cx(
        'flex w-full flex-col items-start gap-3 rounded-xl border border-gray-200/80 bg-white p-4 text-left shadow-card sm:flex-row sm:items-center sm:gap-4 sm:p-5',
        onClick && 'transition-all duration-200 hover:-translate-y-0.5 hover:border-brand-200 hover:shadow-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
      )}
    >
      <IconChip icon={icon} tone={tone} />
      <div className="w-full min-w-0">
        <p className="text-sm text-gray-500">{label}</p>
        <p className="mt-0.5 text-xl font-semibold tracking-tight text-gray-800 sm:text-2xl">{value}</p>
        {sub && <p className="truncate text-xs text-gray-500">{sub}</p>}
        {children}
      </div>
    </Tag>
  )
}
