import { Link, useNavigate } from 'react-router'
import { ArrowRight, CalendarClock, CircleAlert, Clock, Database, History, Lightbulb, Play, Plus, Server, Zap } from 'lucide-react'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateRange, fmtDateTime, fmtNumber, fmtRelative, fmtCreator, fmtUntil } from '../lib/format'
import {
  ACTIVE_STATUSES, Alert, Button, Card, CardHeader, EmptyState, FreshnessBadge, LoadingBlock, ProgressBar, StatCard, StatusBadge,
} from '../components/ui'

const TZ = 'Asia/Jakarta'
const wibDay = (d) => d.toLocaleDateString('en-CA', { timeZone: TZ })

function greeting() {
  const h = Number(new Date().toLocaleString('en-US', { timeZone: TZ, hour: 'numeric', hour12: false }))
  if (h < 11) return 'Selamat pagi'
  if (h < 15) return 'Selamat siang'
  if (h < 18) return 'Selamat sore'
  return 'Selamat malam'
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

export default function DashboardPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data: runsData, loading } = useApi('/runs?limit=100', {
    refreshMs: (d) => (d?.runs?.some((r) => ACTIVE_STATUSES.includes(r.status)) ? 5000 : 30000),
  })
  const { data: jobsData } = useApi('/jobs')
  const { data: meta } = useApi('/runs/meta', { refreshMs: 15000 })
  const { data: schedData } = useApi('/schedules', { refreshMs: 60000 })

  if (loading && !runsData) return <LoadingBlock />
  const runs = runsData?.runs || []
  const jobs = jobsData?.jobs || []
  const today = wibDay(new Date())
  const weekAgo = Date.now() - 7 * 86400e3
  const runsToday = runs.filter((r) => wibDay(new Date(r.created_at)) === today)
  const failed7 = runs.filter((r) => new Date(r.created_at).getTime() >= weekAgo && ['failed', 'interrupted'].includes(r.status))
  const active = runs.filter((r) => ACTIVE_STATUSES.includes(r.status))
  const enabledJobs = jobs.filter((j) => j.enabled).length
  const lateJobs = jobs.filter((j) => j.enabled && j.freshness?.status === 'late')
  const unknownJobs = jobs.filter((j) => j.enabled && j.freshness?.status === 'unknown')
  const isOperator = hasRole(user, 'operator')
  const upcoming = (schedData?.schedules || []).filter((s) => s.enabled && s.next_run_at)
    .sort((a, b) => a.next_run_at.localeCompare(b.next_run_at)).slice(0, 3)
  const firstName = (user.full_name || user.username).split(/[\s.]/)[0]
  const todayLabel = new Date().toLocaleDateString('id-ID', { timeZone: TZ, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-gray-500">{todayLabel}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-gray-800">{greeting()}, {firstName}</h1>
        </div>
        {isOperator && <Button icon={Play} onClick={() => navigate('/runs/new')}>Jalankan Run</Button>}
      </div>

      {active.length > 0 && (
        <Alert tone="info" title={`${active.length} run sedang berjalan / mengantri`}>
          {active.slice(0, 3).map((r, i) => (
            <span key={r.id}>
              {i > 0 && ', '}
              <Link to={`/runs/${r.id}`} className="font-medium underline decoration-brand-300 underline-offset-2 hover:decoration-brand-600">Run #{r.id}</Link>
            </span>
          ))}
          {active.length > 3 && ` dan ${active.length - 3} lainnya`}.
        </Alert>
      )}
      {meta && !meta.worker_online && (
        <Alert tone="warning" title="Worker tidak aktif">Run baru akan tertahan di antrian sampai worker.py dijalankan di server.</Alert>
      )}

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <StatCard icon={Database} tone="mint" label="Job aktif" value={fmtNumber(enabledJobs)} sub={`dari ${fmtNumber(jobs.length)} job terdaftar`} onClick={() => navigate('/jobs')} />
        <StatCard icon={Zap} tone="blue" label="Run hari ini" value={fmtNumber(runsToday.length)} sub={`${fmtNumber(runsToday.reduce((s, r) => s + (r.total_rows || 0), 0))} baris diekspor`} onClick={() => navigate('/runs')} />
        <StatCard icon={CircleAlert} tone={failed7.length ? 'red' : 'gray'} label="Gagal 7 hari" value={fmtNumber(failed7.length)} sub={failed7.length ? 'perlu dicek' : 'semua lancar'} onClick={() => navigate('/runs')} />
        <StatCard icon={Server} tone={meta?.worker_online ? 'mint' : 'amber'} label="Worker" value={meta ? (meta.worker_online ? 'Aktif' : 'Tidak aktif') : '–'} sub={meta?.s3_enabled ? 'upload S3 siap' : 'kredensial S3 belum lengkap'} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="overflow-hidden lg:col-span-2 lg:self-start">
          <CardHeader
            icon={History}
            title="Run terbaru"
            description="Ekspor yang dijalankan lewat Web UI"
            action={<Link to="/runs" className="shrink-0 whitespace-nowrap text-sm font-medium text-brand-700 hover:text-brand-800">Lihat semua</Link>}
          />
          {runs.length === 0 ? (
            <EmptyState icon={History} title="Belum ada run" action={isOperator && <Button icon={Play} onClick={() => navigate('/runs/new')}>Jalankan run pertama</Button>}>
              Run yang dijalankan akan tampil di sini.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-gray-100">
              {runs.slice(0, 6).map((r) => (
                <li key={r.id}>
                  {/* mobile: [#id | info | status] lalu progres selebar baris; sm+: satu baris 4 kolom */}
                  <Link to={`/runs/${r.id}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-2 px-5 py-3.5 transition-colors hover:bg-gray-50 sm:grid-cols-[4rem_1fr_9rem_7rem] sm:gap-x-4">
                    <span className="text-sm font-semibold text-gray-800">#{r.id}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-gray-700">{fmtDateRange(r.start_date, r.end_date)} · {r.job_count} job</span>
                      <span className="block truncate text-xs text-gray-500">{fmtCreator(r.created_by)} · {fmtRelative(r.created_at)}</span>
                    </span>
                    <span className="col-span-3 sm:col-span-1"><ProgressBar value={r.done_items} total={r.total_items} status={r.status} /></span>
                    <span className="col-start-3 row-start-1 flex justify-end sm:col-start-auto sm:row-start-auto"><StatusBadge status={r.status} /></span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-6">
          <Card className="overflow-hidden">
            <CardHeader title="Aksi cepat" />
            <div className="space-y-2 p-4">
              {isOperator && <QuickAction icon={Play} title="Jalankan run" desc="Pilih job & tanggal, cek preview" onClick={() => navigate('/runs/new')} />}
              {isOperator && <QuickAction icon={Plus} title="Tambah job" desc="Wizard dari tabel SQL Server" onClick={() => navigate('/jobs/new')} />}
              <QuickAction icon={Database} title="Daftar job" desc={`${fmtNumber(jobs.length)} job · cari & lihat detail`} onClick={() => navigate('/jobs')} />
              <QuickAction icon={History} title="Riwayat run" desc="Status, log, dan hasil ekspor" onClick={() => navigate('/runs')} />
            </div>
          </Card>

          <Card className="overflow-hidden">
            <CardHeader
              icon={Clock}
              title="Kesegaran data"
              description="Tanggal terbaru yang sudah diproses per job aktif"
              action={<Link to="/jobs" className="shrink-0 whitespace-nowrap text-sm font-medium text-brand-700 hover:text-brand-800">Lihat</Link>}
            />
            <div className="grid grid-cols-3 divide-x divide-gray-100 border-b border-gray-100 text-center">
              {[
                ['Segar', enabledJobs - lateJobs.length - unknownJobs.length, 'text-brand-700'],
                ['Terlambat', lateJobs.length, lateJobs.length ? 'text-red-600' : 'text-gray-800'],
                ['Belum ada', unknownJobs.length, 'text-gray-500'],
              ].map(([label, n, color]) => (
                <div key={label} className="px-2 py-3">
                  <p className={`text-xl font-semibold tabular-nums ${color}`}>{fmtNumber(n)}</p>
                  <p className="text-xs text-gray-500">{label}</p>
                </div>
              ))}
            </div>
            {lateJobs.length === 0 ? (
              <p className="px-5 py-4 text-sm text-gray-500">{unknownJobs.length ? 'Tidak ada yang terlambat. Job "belum ada" baru tercatat setelah run pertama dengan versi ini.' : 'Semua job aktif sudah memproses data terbaru.'}</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {lateJobs.slice(0, 5).map((j) => (
                  <li key={j.name}>
                    <Link to={`/jobs/${encodeURIComponent(j.name)}`} className="flex items-center justify-between gap-3 px-5 py-2.5 hover:bg-gray-50">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-gray-800">{j.name}</span>
                        <span className="block text-xs text-gray-500">terbaru {j.freshness.latest} · seharusnya {j.freshness.expected}</span>
                      </span>
                      <FreshnessBadge freshness={j.freshness} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="overflow-hidden">
            <CardHeader
              icon={CalendarClock}
              title="Jadwal berikutnya"
              action={<Link to="/schedules" className="shrink-0 whitespace-nowrap text-sm font-medium text-brand-700 hover:text-brand-800">Kelola</Link>}
            />
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
                    <p className="mt-0.5 text-xs text-gray-500">{fmtDateTime(s.next_run_at)} · {s.job_count} job</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div className="rounded-xl border border-brand-100 bg-brand-50 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-brand-900">
              <Lightbulb className="h-4 w-4 text-brand-600" aria-hidden /> Tips
            </div>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed text-brand-900/80">
              <li>Job baru tersimpan <strong>nonaktif</strong> — aktifkan setelah uji coba berhasil.</li>
              <li>Job aktif berawalan <span className="font-mono text-xs">bronze_</span> otomatis ikut jadwal harian.</li>
              <li>Run yang gagal bisa diulang dari halaman detail run.</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
