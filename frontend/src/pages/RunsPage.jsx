import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { ChevronLeft, ChevronRight, History, Play, RotateCcw, Search, SearchX, X } from 'lucide-react'
import { ACTIVE_STATUSES, Alert, Button, Card, EmptyState, LoadingBlock, PageHeader, ProgressBar, StatusBadge, cx, inputClass } from '../components/ui'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateRange, fmtDateTime, fmtDuration, fmtNumber, fmtRelative, runElapsed, fmtCreator } from '../lib/format'

const PAGE_SIZE = 50
const selectClass = `${inputClass} pr-8`

// Pilihan filter status -> nilai `status` di API (dipisah koma).
const STATUS_FILTERS = [
  ['', 'Semua status'],
  ['active', 'Berjalan / mengantri', 'queued,running'],
  ['problem', 'Bermasalah (gagal / terputus)', 'failed,interrupted'],
  ['success', 'Sukses', 'success'],
  ['failed', 'Gagal', 'failed'],
  ['interrupted', 'Terputus', 'interrupted'],
  ['cancelled', 'Dibatalkan', 'cancelled'],
]
// Filter `by` di URL: '@manual' / '@schedule' = semua run manual / jadwal, selain itu = created_by persis.
// from/to = rentang tanggal DATA run; cfrom/cto = tanggal run DIBUAT (kalender WIB).
const FILTER_KEYS = ['q', 'status', 'by', 'from', 'to', 'cfrom', 'cto']

function apiPath(params, page) {
  const p = new URLSearchParams({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
  const q = params.get('q')?.trim()
  if (q) p.set('q', q)
  const status = STATUS_FILTERS.find(([k]) => k && k === params.get('status'))
  if (status) p.set('status', status[2])
  const by = params.get('by')
  if (by === '@manual' || by === '@schedule') p.set('source', by.slice(1))
  else if (by) p.set('created_by', by)
  if (params.get('from')) p.set('date_from', params.get('from'))
  if (params.get('to')) p.set('date_to', params.get('to'))
  if (params.get('cfrom')) p.set('created_from', params.get('cfrom'))
  if (params.get('cto')) p.set('created_to', params.get('cto'))
  return `/runs?${p}`
}

function DateRange({ label, title, from, to, onFrom, onTo }) {
  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
      <span className="text-sm text-gray-500" title={title}>{label}</span>
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <input type="date" aria-label={`${label} dari`} className={`${inputClass} min-w-0 sm:w-40`} value={from} max={to || undefined} onChange={(e) => onFrom(e.target.value)} />
        <span className="text-gray-400" aria-hidden>–</span>
        <input type="date" aria-label={`${label} sampai`} className={`${inputClass} min-w-0 sm:w-40`} value={to} min={from || undefined} onChange={(e) => onTo(e.target.value)} />
      </div>
    </div>
  )
}

export default function RunsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  // Filter disimpan di URL: tetap ada saat kembali dari detail run, dan bisa dibagikan sebagai link.
  const [params, setParams] = useSearchParams()
  const page = Math.max(1, parseInt(params.get('page'), 10) || 1)
  const filtered = FILTER_KEYS.some((k) => params.get(k))

  const { data, error, loading } = useApi(apiPath(params, page), {
    // Lebih sering refresh selama ada run yang mengantri/berjalan.
    refreshMs: (d) => (d?.runs?.some((r) => ACTIVE_STATUSES.includes(r.status)) ? 3000 : 15000),
  })
  const { data: creatorData } = useApi('/runs/creators')
  const runs = data?.runs || []
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Ubah filter -> kembali ke halaman 1. replace: mengetik/memilih filter tidak menumpuk riwayat browser.
  const update = (changes) => setParams((prev) => {
    const next = new URLSearchParams(prev)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    if (!('page' in changes)) next.delete('page')
    return next
  }, { replace: true })

  // Kotak cari: state lokal supaya ketikan responsif, URL (= request API) diperbarui setelah jeda.
  const urlQ = params.get('q') || ''
  const [qInput, setQInput] = useState(urlQ)
  const qTimer = useRef()
  useEffect(() => setQInput(urlQ), [urlQ])
  useEffect(() => () => clearTimeout(qTimer.current), [])
  const onSearch = (v) => {
    setQInput(v)
    clearTimeout(qTimer.current)
    qTimer.current = setTimeout(() => update({ q: v }), 350)
  }
  const reset = () => {
    clearTimeout(qTimer.current)
    setQInput('')
    setParams({}, { replace: true })
  }
  const goPage = (n) => {
    update({ page: n > 1 ? String(n) : '' })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const creators = useMemo(() => {
    const names = (creatorData?.creators || []).map((c) => c.created_by)
    const by = params.get('by')
    if (by && !by.startsWith('@') && !names.includes(by)) names.push(by) // dari URL, belum ada di daftar
    return {
      schedules: names.filter((n) => n.startsWith('jadwal:')),
      users: names.filter((n) => !n.startsWith('jadwal:')),
    }
  }, [creatorData, params])

  const back = { back: `${location.pathname}${location.search}` }
  const first = total ? (page - 1) * PAGE_SIZE + 1 : 0
  const last = (page - 1) * PAGE_SIZE + runs.length

  return (
    <>
      <PageHeader
        title="Riwayat Run"
        description="Semua ekspor yang dijalankan lewat Web UI, terbaru di atas."
        actions={hasRole(user, 'operator') && (
          <Button icon={Play} onClick={() => navigate('/runs/new')}>Jalankan Run Baru</Button>
        )}
      />
      {error && <Alert tone="error" className="mb-4">{error.message}</Alert>}

      <Card className="overflow-hidden">
        <div className="space-y-3 border-b border-gray-200 p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_15rem_15rem]">
            <div className="relative sm:col-span-2 lg:col-span-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
              <input type="search" aria-label="Cari run" placeholder="Cari nama job atau nomor run (#123)…" className={`${inputClass} pl-9`} value={qInput} onChange={(e) => onSearch(e.target.value)} />
            </div>
            <select aria-label="Filter status" className={selectClass} value={params.get('status') || ''} onChange={(e) => update({ status: e.target.value })}>
              {STATUS_FILTERS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <select aria-label="Filter pembuat run" className={selectClass} value={params.get('by') || ''} onChange={(e) => update({ by: e.target.value })}>
              <option value="">Semua pembuat</option>
              <option value="@schedule">Semua jadwal otomatis</option>
              <option value="@manual">Semua run manual</option>
              {creators.schedules.length > 0 && (
                <optgroup label="Jadwal">
                  {creators.schedules.map((n) => <option key={n} value={n}>{n.slice(7)}</option>)}
                </optgroup>
              )}
              {creators.users.length > 0 && (
                <optgroup label="Pengguna">
                  {creators.users.map((n) => <option key={n} value={n}>{n}</option>)}
                </optgroup>
              )}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <DateRange
              label="Tanggal data"
              title="Tanggal data yang diekspor (kolom Tanggal data): run yang rentang datanya beririsan dengan rentang ini"
              from={params.get('from') || ''} to={params.get('to') || ''}
              onFrom={(v) => update({ from: v })} onTo={(v) => update({ to: v })}
            />
            <DateRange
              label="Dibuat"
              title="Tanggal run dibuat/dijalankan (kolom Dibuat), menurut kalender WIB"
              from={params.get('cfrom') || ''} to={params.get('cto') || ''}
              onFrom={(v) => update({ cfrom: v })} onTo={(v) => update({ cto: v })}
            />
            {filtered && (
              <Button variant="ghost" size="sm" icon={X} onClick={reset} className="xl:ml-auto">Reset filter</Button>
            )}
          </div>
        </div>

        {loading && !data ? (
          <LoadingBlock />
        ) : runs.length === 0 ? (
          filtered || total > 0 ? (
            <EmptyState
              icon={SearchX}
              title={total > 0 ? 'Halaman ini kosong' : 'Tidak ada run yang cocok'}
              action={total > 0
                ? <Button variant="secondary" onClick={() => goPage(1)}>Ke halaman pertama</Button>
                : <Button variant="secondary" icon={X} onClick={reset}>Reset filter</Button>}
            >
              {total === 0 && 'Ubah kata kunci, status, pembuat, atau rentang tanggalnya.'}
            </EmptyState>
          ) : (
            <EmptyState icon={History} title="Belum ada run">
              {hasRole(user, 'operator') ? 'Mulai dengan menekan "Jalankan Run Baru".' : 'Run yang dijalankan operator akan tampil di sini.'}
            </EmptyState>
          )
        ) : (
          <div className={cx('transition-opacity', loading && 'opacity-60')}>
            {/* Tabel: tablet & desktop */}
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/80 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-3">Run</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Tanggal data</th>
                    <th className="px-4 py-3 text-right">Job</th>
                    <th className="w-48 px-4 py-3">Progres</th>
                    <th className="px-4 py-3 text-right">Baris</th>
                    <th className="px-4 py-3 text-right">Durasi</th>
                    <th className="px-4 py-3">Dibuat</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {runs.map((r) => (
                    <tr
                      key={r.id}
                      onClick={() => navigate(`/runs/${r.id}`, { state: back })}
                      className="cursor-pointer transition-colors hover:bg-gray-50"
                    >
                      <td className="whitespace-nowrap px-4 py-3">
                        <Link to={`/runs/${r.id}`} state={back} className="font-medium text-gray-800 hover:text-brand-600" onClick={(e) => e.stopPropagation()}>
                          #{r.id}
                        </Link>
                        {r.retry_of && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs text-gray-500" title={`Ulangan dari run #${r.retry_of}`}>
                            <RotateCcw className="h-3 w-3" aria-hidden />#{r.retry_of}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-600">{fmtDateRange(r.start_date, r.end_date)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-600">{r.job_count}</td>
                      <td className="px-4 py-3"><ProgressBar value={r.done_items} total={r.total_items} status={r.status} /></td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-600">{fmtNumber(r.total_rows)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-gray-600">{fmtDuration(runElapsed(r))}</td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <p className="text-gray-700">{fmtCreator(r.created_by)}</p>
                        <p className="text-xs text-gray-500" title={fmtDateTime(r.created_at)}>{fmtRelative(r.created_at)}</p>
                      </td>
                      <td className="pr-3 text-gray-300"><ChevronRight className="h-4 w-4" aria-hidden /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Kartu: mobile */}
            <ul className="divide-y divide-gray-100 md:hidden">
              {runs.map((r) => (
                <li key={r.id}>
                  <Link to={`/runs/${r.id}`} state={back} className="block space-y-3 px-4 py-4 active:bg-gray-50">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-gray-800">Run #{r.id}</span>
                      <StatusBadge status={r.status} />
                    </div>
                    <div className="flex justify-between text-sm text-gray-600">
                      <span>{fmtDateRange(r.start_date, r.end_date)}</span>
                      <span>{r.job_count} job · {fmtNumber(r.total_rows)} baris</span>
                    </div>
                    <ProgressBar value={r.done_items} total={r.total_items} status={r.status} />
                    <p className="text-xs text-gray-500">{fmtCreator(r.created_by)} · {fmtRelative(r.created_at)}</p>
                  </Link>
                </li>
              ))}
            </ul>

            <div className="flex flex-col gap-3 border-t border-gray-100 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-gray-500">
                Menampilkan <span className="tabular-nums">{fmtNumber(first)}–{fmtNumber(last)}</span> dari{' '}
                <span className="tabular-nums">{fmtNumber(total)}</span> run{filtered && ' yang cocok'}
              </p>
              {pages > 1 && (
                <div className="flex items-center gap-2">
                  <Button variant="secondary" size="sm" icon={ChevronLeft} disabled={page <= 1} onClick={() => goPage(page - 1)}>
                    Sebelumnya
                  </Button>
                  <span className="px-1 text-sm tabular-nums text-gray-500">{page} / {pages}</span>
                  <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => goPage(page + 1)}>
                    Berikutnya <ChevronRight className="h-4 w-4" aria-hidden />
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </Card>
    </>
  )
}
