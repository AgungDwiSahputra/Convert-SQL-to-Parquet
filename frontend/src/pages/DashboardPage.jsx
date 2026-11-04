import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  AlertTriangle, ArrowRight, BarChart3, BellRing, BookOpen, CalendarClock, CheckCircle2, CircleAlert, Database, Download, History, Info,
  Layers, Play, Plus, ShieldCheck, Zap,
} from 'lucide-react'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDate, fmtDateRange, fmtDateTime, fmtNumber, fmtRelative, fmtCreator, fmtUntil } from '../lib/format'
import {
  ACTIVE_STATUSES, Alert, Button, Card, CardHeader, EmptyState, LoadingBlock, ProgressBar, StatCard, StatusBadge, cx,
} from '../components/ui'

const TZ = 'Asia/Jakarta'
const BLN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']

function greeting() {
  const h = Number(new Date().toLocaleString('en-US', { timeZone: TZ, hour: 'numeric', hour12: false }))
  if (h < 11) return 'Selamat pagi'
  if (h < 15) return 'Selamat siang'
  if (h < 18) return 'Selamat sore'
  return 'Selamat malam'
}

/** 214880 -> "215 rb", 1250000 -> "1,3 jt" (sumbu grafik). */
function fmtCompact(n) {
  if (n >= 1e6) return `${(n / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 1 })} jt`
  if (n >= 1e3) return `${Math.round(n / 1e3).toLocaleString('id-ID')} rb`
  return String(n)
}

/** Batas atas sumbu yang "bulat": 1 / 2 / 5 x 10^k. */
function niceMax(v) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  return [1, 2, 5, 10].map((m) => m * p).find((x) => x >= v)
}

// --- Banner kesehatan ------------------------------------------------------------------------------

const LEVEL = {
  ok: { icon: CheckCircle2, box: 'border-brand-200 bg-brand-50', chip: 'bg-brand-600 text-white', title: 'text-brand-900', text: 'text-brand-900/80' },
  warning: { icon: AlertTriangle, box: 'border-amber-200 bg-amber-50', chip: 'bg-amber-500 text-white', title: 'text-amber-900', text: 'text-amber-900/80' },
  critical: { icon: CircleAlert, box: 'border-red-200 bg-red-50', chip: 'bg-red-600 text-white', title: 'text-red-900', text: 'text-red-900/80' },
}

function Pill({ ok, children }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-xs text-gray-700">
      <span className={cx('h-1.5 w-1.5 rounded-full', ok ? 'bg-brand-500' : 'bg-amber-500')} aria-hidden />
      {children}
    </span>
  )
}

function HealthBanner({ level, title, text, next }) {
  const s = LEVEL[level]
  return (
    <div className={cx('flex flex-col gap-4 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5', s.box)}>
      <div className="flex items-start gap-3">
        <span className={cx('flex h-10 w-10 shrink-0 items-center justify-center rounded-full', s.chip)}>
          <s.icon className="h-5 w-5" aria-hidden />
        </span>
        <div>
          <p className={cx('font-semibold', s.title)}>{title}</p>
          <p className={cx('mt-0.5 text-sm', s.text)}>{text}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 sm:justify-end">
        <Pill ok={!!next}>{next ? `Proses berikutnya ${fmtUntil(next.next_run_at)}` : 'Belum ada jadwal aktif'}</Pill>
      </div>
    </div>
  )
}

// --- Daftar "Perlu perhatian" ----------------------------------------------------------------------

const SEVERITY = {
  critical: { icon: CircleAlert, chip: 'bg-red-50 text-red-600', label: 'Penting' },
  warning: { icon: AlertTriangle, chip: 'bg-amber-50 text-amber-600', label: 'Perlu dicek' },
  info: { icon: Info, chip: 'bg-blue-50 text-blue-600', label: 'Info' },
}

function AttentionList({ items, emptyText = 'Tidak ada catatan. Semua data aktif sudah terbaru dan lengkap.' }) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, 6)
  if (items.length === 0) {
    return (
      <div className="flex items-center gap-3 px-5 py-6 text-sm text-gray-600">
        <CheckCircle2 className="h-5 w-5 shrink-0 text-brand-600" aria-hidden />
        {emptyText}
      </div>
    )
  }
  return (
    <>
      <ul className="divide-y divide-gray-100">
        {shown.map((it) => {
          const s = SEVERITY[it.severity]
          const body = (
            <>
              <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-lg', s.chip)} title={s.label}>
                <s.icon className="h-4 w-4" aria-label={s.label} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-gray-800">{it.title}</span>
                <span className="line-clamp-2 text-xs text-gray-500 sm:line-clamp-1" title={it.desc}>{it.desc}</span>
              </span>
              {it.to && <ArrowRight className="h-4 w-4 shrink-0 text-gray-300 transition-colors group-hover:text-brand-600" aria-hidden />}
            </>
          )
          return (
            <li key={it.key}>
              {it.to
                ? <Link to={it.to} className="group flex items-center gap-3 px-5 py-3 transition-colors hover:bg-gray-50">{body}</Link>
                : <div className="flex items-center gap-3 px-5 py-3">{body}</div>}
            </li>
          )
        })}
      </ul>
      {items.length > 6 && (
        <button type="button" onClick={() => setAll((v) => !v)} className="w-full border-t border-gray-100 px-5 py-2.5 text-sm font-medium text-brand-700 hover:bg-gray-50">
          {all ? 'Tampilkan lebih sedikit' : `Tampilkan semua (${items.length})`}
        </button>
      )}
    </>
  )
}

// --- Grafik data ter-load per tanggal ----------------------------------------------------------------

function LoadChart({ daily, dataDate }) {
  const [hover, setHover] = useState(null)
  const top = niceMax(Math.max(...daily.map((d) => d.rows), 0))
  const ticks = [top, top / 2, 0]
  const total = daily.reduce((s, d) => s + d.rows, 0)
  const unit = 'data'
  const held = 'ditahan pemeriksaan kualitas'
  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={BarChart3}
        title="Data masuk per tanggal"
        description={`Baris yang sudah diproses per tanggal data · 14 hari · total ${fmtNumber(total)} baris`}
      />
      <div className="px-4 pb-4 pt-5 sm:px-5">
        <div className="flex gap-2">
          {/* sumbu Y */}
          <div className="relative h-40 w-10 shrink-0 text-right text-[11px] tabular-nums text-gray-400">
            {ticks.map((t, i) => (
              <span key={t} className="absolute right-0 -translate-y-1/2" style={{ top: `${(i / 2) * 100}%` }}>{fmtCompact(t)}</span>
            ))}
          </div>
          {/* area plot */}
          <div className="relative min-w-0 flex-1">
            <div className="relative h-40">
              {ticks.map((t, i) => (
                <div key={t} className="absolute inset-x-0 border-t border-gray-100" style={{ top: `${(i / 2) * 100}%` }} aria-hidden />
              ))}
              <div className="absolute inset-0 flex items-end">
                {daily.map((d, i) => {
                  const h = d.rows ? Math.max((d.rows / top) * 100, 1.5) : 0
                  const isData = d.date === dataDate
                  return (
                    <button
                      key={d.date}
                      type="button"
                      onMouseEnter={() => setHover(i)}
                      onMouseLeave={() => setHover(null)}
                      onFocus={() => setHover(i)}
                      onBlur={() => setHover(null)}
                      aria-label={`${fmtDate(d.date)}: ${fmtNumber(d.rows)} baris, ${d.jobs} ${unit} diproses${d.blocked ? `, ${d.blocked} ${held}` : ''}`}
                      className="group relative flex h-full flex-1 items-end justify-center px-px focus-visible:outline-none"
                    >
                      {d.jobs === 0 && !d.blocked
                        ? <span className="h-0.5 w-full max-w-6 rounded-full bg-gray-200" />
                        : (
                          <span
                            className={cx('w-full max-w-6 rounded-t-[4px] transition-colors group-focus-visible:ring-2 group-focus-visible:ring-brand-500',
                              hover === i ? 'bg-brand-700' : isData ? 'bg-brand-600' : 'bg-brand-600/70')}
                            style={{ height: `${h}%` }}
                          />
                        )}
                    </button>
                  )
                })}
              </div>
              {hover !== null && (
                <div
                  className={cx('pointer-events-none absolute -top-2 z-10 w-max -translate-y-full rounded-lg bg-gray-800 px-3 py-2 text-xs text-white shadow-raised',
                    hover < 3 ? '' : hover > daily.length - 4 ? '-translate-x-full' : '-translate-x-1/2')}
                  style={{ left: `${((hover + (hover < 3 ? 0 : hover > daily.length - 4 ? 1 : 0.5)) / daily.length) * 100}%` }}
                  role="status"
                >
                  <p className="font-semibold">{fmtDate(daily[hover].date)}</p>
                  <p className="mt-0.5 text-white/80">{fmtNumber(daily[hover].rows)} baris · {daily[hover].jobs} {unit} diproses</p>
                  {daily[hover].blocked > 0 && <p className="mt-0.5 text-red-200">{daily[hover].blocked} {unit} {held}</p>}
                  {daily[hover].jobs === 0 && !daily[hover].blocked && <p className="mt-0.5 text-white/80">Belum ada yang diproses</p>}
                </div>
              )}
            </div>
            {/* sumbu X */}
            <div className="mt-2 flex">
              {daily.map((d, i) => {
                const [, m, day] = d.date.split('-').map(Number)
                const showMonth = i === 0 || day === 1
                return (
                  <div key={d.date} className="flex flex-1 flex-col items-center gap-0.5">
                    <span className={cx('text-[11px] tabular-nums', d.date === dataDate ? 'font-semibold text-gray-800' : 'text-gray-400')}>
                      {day}{showMonth && <span className="hidden sm:inline"> {BLN[m - 1]}</span>}
                    </span>
                    {d.blocked > 0 && <CircleAlert className="h-3 w-3 text-red-500" aria-label={`ada yang ${held}`} />}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-brand-600" aria-hidden /> baris masuk</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-3 rounded-full bg-gray-300" aria-hidden /> belum ada yang diproses</span>
          <span className="inline-flex items-center gap-1.5"><CircleAlert className="h-3 w-3 text-red-500" aria-hidden /> ada yang {held}</span>
        </p>
      </div>
      <table className="sr-only">
        <caption>Data masuk per tanggal, 14 hari</caption>
        <thead><tr><th>Tanggal</th><th>Baris</th><th>Data diproses</th><th>Ditahan pemeriksaan kualitas</th></tr></thead>
        <tbody>{daily.map((d) => <tr key={d.date}><td>{d.date}</td><td>{d.rows}</td><td>{d.jobs}</td><td>{d.blocked}</td></tr>)}</tbody>
      </table>
    </Card>
  )
}

function QuickAction({ icon: Icon, title, desc, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex w-full items-center gap-3 rounded-xl border border-gray-200/80 p-3 text-left transition-all hover:border-brand-200 hover:bg-brand-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 transition-colors group-hover:bg-brand-100 group-hover:text-brand-700">
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-gray-800">{title}</span>
        <span className="block truncate text-xs text-gray-500">{desc}</span>
      </span>
      <ArrowRight className="h-4 w-4 text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-brand-600" aria-hidden />
    </button>
  )
}

// --- Halaman ---------------------------------------------------------------------------------------

/** Data Beranda. */
function useDashboardData() {
  const { data: runsData, loading } = useApi('/runs?limit=100', {
    refreshMs: (d) => (d?.runs?.some((r) => ACTIVE_STATUSES.includes(r.status)) ? 5000 : 30000),
  })
  const { data: jobsData } = useApi('/jobs', { refreshMs: 60000 })
  const { data: meta } = useApi('/runs/meta', { refreshMs: 15000 })
  const { data: schedData } = useApi('/schedules', { refreshMs: 60000 })
  const { data: dash } = useApi('/dashboard', { refreshMs: 60000 })
  const runs = runsData?.runs || []
  const jobs = jobsData?.jobs || []
  const active = jobs.filter((j) => j.enabled)
  const weekAgo = Date.now() - 7 * 86400e3
  const runs7 = runs.filter((r) => new Date(r.created_at).getTime() >= weekAgo)
  const dataDate = dash?.data_date
  const dq = dash?.dq7
  return {
    loading: loading && !runsData, runs, jobs, meta, dash, active, runs7, dataDate, dq,
    fresh: active.filter((j) => j.freshness?.status === 'fresh'),
    late: active.filter((j) => j.freshness?.status === 'late'),
    unknown: active.filter((j) => j.freshness?.status === 'unknown'),
    gapJobs: active.filter((j) => j.freshness?.gaps?.count),
    failed7: runs7.filter((r) => ['failed', 'interrupted'].includes(r.status)),
    running: runs.filter((r) => ACTIVE_STATUSES.includes(r.status)),
    upcoming: (schedData?.schedules || []).filter((s) => s.enabled && s.next_run_at)
      .sort((a, b) => a.next_run_at.localeCompare(b.next_run_at)).slice(0, 3),
    judged: dq ? dq.ok + dq.warning + dq.blocked : 0,
    today: dash?.daily?.find((d) => d.date === dataDate),
  }
}

const todayLabel = () => new Date().toLocaleDateString('id-ID', { timeZone: TZ, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
const firstNameOf = (user) => (user.full_name || user.username).split(/[\s.]/)[0]

// SATU tampilan untuk semua peran, istilah sehari-hari (job -> data, run -> proses, DQ -> pemeriksaan kualitas). Bagian teknis
// muncul bila server mengirimnya: pesan pemeriksaan DQ (Operator/Admin -- Viewer tidak menerimanya, simple_view.dashboard_issue) dan
// akun yang menunggu persetujuan unduh (Admin). Tombol & pintasan aksi khusus Operator.

export default function DashboardPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const d = useDashboardData()
  if (d.loading) return <LoadingBlock />
  const { runs, meta, dash, active, fresh, late, unknown, gapJobs, runs7, failed7, running, upcoming, dataDate, dq, judged, today } = d
  const isOperator = hasRole(user, 'operator')
  const job = (j) => `/jobs/${encodeURIComponent(j)}`

  const items = []
  if (meta && !meta.worker_online) {
    items.push({ key: 'worker', severity: 'critical', title: 'Proses otomatis sedang berhenti', desc: 'Data baru belum masuk sampai proses otomatis (worker) berjalan kembali.' })
  }
  if (meta && !meta.s3_enabled) {
    items.push({ key: 's3', severity: 'warning', title: 'Pengiriman ke datalake belum diatur', desc: 'Data diproses tetapi belum dikirim ke datalake (pengaturan penyimpanan S3 di server belum lengkap).' })
  }
  for (const r of failed7.slice(0, 3)) {
    items.push({ key: `run${r.id}`, severity: 'critical', title: `Proses No. ${r.id} ${r.status === 'interrupted' ? 'terputus' : 'gagal'}`, desc: `Tanggal data ${fmtDateRange(r.start_date, r.end_date)} · ${fmtRelative(r.created_at)}`, to: `/runs/${r.id}` })
  }
  for (const b of (dash?.blocked || []).slice(0, 5)) {
    items.push({ key: `blk${b.job_name}${b.target_date}`, severity: 'critical', title: `Ditahan pemeriksaan kualitas: ${b.label}`, desc: `Data ${fmtDate(b.target_date)} belum diterbitkan karena tidak lolos pemeriksaan kualitas.${b.message ? ` ${b.message}` : ''}`, to: b.run_id ? `/runs/${b.run_id}` : job(b.job_name) })
  }
  for (const j of late.slice(0, 5)) {
    items.push({ key: `late${j.name}`, severity: 'warning', title: `Belum terbaru: ${j.label}`, desc: `Data terakhir ${fmtDate(j.freshness.latest)}, seharusnya sudah ${fmtDate(j.freshness.expected)}.`, to: job(j.name) })
  }
  for (const j of gapJobs.slice(0, 5)) {
    const g = j.freshness.gaps
    items.push({ key: `gap${j.name}`, severity: 'warning', title: `Ada tanggal yang belum masuk: ${j.label}`, desc: `${g.items[0].label}${g.count > 1 ? ` dan ${g.count - 1} periode lain` : ''}`, to: job(j.name) })
  }
  if (late.length > 5 || gapJobs.length > 5) {
    items.push({ key: 'more', severity: 'warning', title: `${Math.max(late.length - 5, 0) + Math.max(gapJobs.length - 5, 0)} data lain belum lengkap`, desc: 'Lihat semuanya di Daftar Data.', to: '/jobs' })
  }
  if (dash?.warnings?.length) {
    const w = dash.warnings
    const desc = w[0].message
      ? w.slice(0, 2).map((x) => `${x.label} ${fmtDate(x.target_date)}: ${x.message}`).join(' · ')
      : `Data tetap diterbitkan. Contoh: ${[...new Set(w.map((x) => x.label))].slice(0, 3).join(', ')}`
    items.push({ key: 'dqw', severity: 'info', title: `${w.length}${w.length >= 20 ? '+' : ''} catatan pemeriksaan kualitas (7 hari)`, desc, to: w[0].message && w[0].run_id ? `/runs/${w[0].run_id}` : null })
  }
  if (dash?.pending_export) {
    items.push({ key: 'pending', severity: 'info', title: `${dash.pending_export} akun baru menunggu persetujuan Unduh Data`, desc: 'Akun yang daftar otomatis lewat SSO (Viewer). Setujui di halaman Users bila memang boleh mengunduh data.', to: '/users' })
  }
  if (unknown.length) {
    items.push({ key: 'unknown', severity: 'info', title: `${unknown.length} data belum pernah diproses`, desc: 'Akan terisi setelah proses pertama berjalan.', to: '/jobs' })
  }

  const urgent = items.filter((i) => i.severity !== 'info').length
  const level = items.some((i) => i.severity === 'critical') ? 'critical' : urgent ? 'warning' : 'ok'
  const bannerTitle = level === 'ok' ? 'Semua data sudah terbaru' : `${urgent} hal perlu diketahui`
  const bannerText = level === 'ok'
    ? `Data ${dataDate ? fmtDate(dataDate) : 'terbaru'} sudah tersedia untuk ${fresh.length} dari ${active.length} data.`
    : 'Rinciannya ada di daftar di bawah, mulai dari yang bertanda merah.'

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-gray-500">{todayLabel()}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-gray-800">{greeting()}, {firstNameOf(user)}</h1>
          <p className="mt-1 text-sm text-gray-500">Ringkasan kondisi data terbaru.</p>
        </div>
        {isOperator && <Button icon={Play} onClick={() => navigate('/runs/new')}>Jalankan Proses</Button>}
      </div>

      <HealthBanner level={level} title={bannerTitle} text={bannerText} next={upcoming[0]} />

      {running.length > 0 && <Alert tone="info">{running.length} proses sedang berjalan — data terbaru sedang dimasukkan.</Alert>}

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <StatCard icon={Database} tone={late.length ? 'amber' : 'mint'} label={dataDate ? `Data ${fmtDate(dataDate)}` : 'Data terbaru'} value={`${fresh.length}/${active.length}`} sub="data sudah tersedia" onClick={() => navigate('/jobs')}>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-gray-100" aria-hidden>
            <div className="h-full rounded-full bg-brand-500" style={{ width: `${active.length ? (fresh.length / active.length) * 100 : 0}%` }} />
          </div>
        </StatCard>
        <StatCard icon={ShieldCheck} tone={dq?.blocked ? 'red' : dq?.warning ? 'amber' : 'mint'} label="Pemeriksaan kualitas · 7 hari" value={judged ? `${Math.round((dq.ok / judged) * 100)}% lolos` : '–'} sub={dq ? `${fmtNumber(dq.warning)} catatan · ${fmtNumber(dq.blocked)} ditahan` : 'memuat…'} />
        <StatCard icon={Zap} tone={failed7.length ? 'red' : 'blue'} label="Proses · 7 hari" value={fmtNumber(runs7.length)} sub={`${fmtNumber(runs7.length - failed7.length)} berhasil · ${fmtNumber(failed7.length)} gagal`} onClick={() => navigate('/runs')} />
        <StatCard icon={Layers} tone="blue" label={dataDate ? `Baris masuk ${fmtDate(dataDate)}` : 'Baris masuk'} value={today ? fmtNumber(today.rows) : '–'} sub={today ? `dari ${fmtNumber(today.jobs)} data` : 'memuat…'} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <Card className="overflow-hidden">
            <CardHeader icon={BellRing} title="Perlu diketahui" description="Data yang belum terbaru atau belum lengkap, paling penting di atas" />
            <AttentionList items={items} />
          </Card>

          {dash?.daily && <LoadChart daily={dash.daily} dataDate={dataDate} />}

          <Card className="overflow-hidden">
            <CardHeader icon={History} title="Proses terbaru" description="Pemasukan data terbaru, otomatis maupun manual" action={<Link to="/runs" className="shrink-0 whitespace-nowrap text-sm font-medium text-brand-700 hover:text-brand-800">Lihat semua</Link>} />
            {runs.length === 0 ? (
              <EmptyState icon={History} title="Belum ada proses" action={isOperator && <Button icon={Play} onClick={() => navigate('/runs/new')}>Jalankan proses pertama</Button>}>
                Proses pemasukan data akan tampil di sini.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-gray-100">
                {runs.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    <Link to={`/runs/${r.id}`} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 px-5 py-3.5 transition-colors hover:bg-gray-50 sm:grid-cols-[1fr_9rem_7rem] sm:gap-x-4">
                      <span className="min-w-0">
                        <span className="block truncate text-sm text-gray-700">Data {fmtDateRange(r.start_date, r.end_date)} · {r.job_count} data</span>
                        <span className="block truncate text-xs text-gray-500">No. {r.id} · {fmtCreator(r.created_by)} · {fmtRelative(r.created_at)}</span>
                      </span>
                      <span className="col-span-2 sm:col-span-1"><ProgressBar value={r.done_items} total={r.total_items} status={r.status} /></span>
                      <span className="col-start-2 row-start-1 flex justify-end sm:col-start-auto sm:row-start-auto"><StatusBadge status={r.status} /></span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-6">
          <Card className="overflow-hidden">
            <CardHeader icon={CalendarClock} title="Proses terjadwal berikutnya" action={<Link to="/schedules" className="shrink-0 whitespace-nowrap text-sm font-medium text-brand-700 hover:text-brand-800">Lihat</Link>} />
            {upcoming.length === 0 ? (
              <p className="px-5 py-4 text-sm text-gray-500">Belum ada jadwal aktif.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {upcoming.map((s) => (
                  <li key={s.id} className="px-5 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-medium text-gray-800">{s.name}</span>
                      <span className="shrink-0 text-xs font-medium text-brand-700">{fmtUntil(s.next_run_at)}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-gray-500">{fmtDateTime(s.next_run_at)} · {s.job_count} data</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="overflow-hidden">
            <CardHeader title="Pintasan" />
            <div className="space-y-2 p-4">
              {isOperator && <QuickAction icon={Play} title="Jalankan proses" desc="Pilih data & tanggal, cek pratinjau" onClick={() => navigate('/runs/new')} />}
              {isOperator && <QuickAction icon={Plus} title="Tambah data" desc="Daftarkan tabel SQL Server baru" onClick={() => navigate('/jobs/new')} />}
              <QuickAction icon={Database} title="Daftar data" desc={`${fmtNumber(active.length)} data aktif · kesegaran tiap data`} onClick={() => navigate('/jobs')} />
              <QuickAction icon={History} title="Riwayat proses" desc="Hasil setiap pemasukan data" onClick={() => navigate('/runs')} />
              <QuickAction icon={BookOpen} title="Knowledge Base" desc="Dokumen untuk asisten AI" onClick={() => navigate('/kb')} />
              {user.can_export && <QuickAction icon={Download} title="Unduh data" desc="Unduh data per tanggal" onClick={() => navigate('/export')} />}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
