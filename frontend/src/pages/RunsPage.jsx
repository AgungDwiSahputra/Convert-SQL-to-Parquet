import { Link, useNavigate } from 'react-router'
import { ChevronRight, History, Play, RotateCcw } from 'lucide-react'
import { ACTIVE_STATUSES, Alert, Button, Card, EmptyState, LoadingBlock, PageHeader, ProgressBar, StatusBadge } from '../components/ui'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateRange, fmtDateTime, fmtDuration, fmtNumber, fmtRelative, runElapsed, fmtCreator } from '../lib/format'

export default function RunsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data, error, loading } = useApi('/runs?limit=100', {
    // Lebih sering refresh selama ada run yang mengantri/berjalan.
    refreshMs: (d) => (d?.runs?.some((r) => ACTIVE_STATUSES.includes(r.status)) ? 3000 : 15000),
  })
  const runs = data?.runs || []

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
        {loading && !data ? (
          <LoadingBlock />
        ) : runs.length === 0 ? (
          <EmptyState icon={History} title="Belum ada run">
            {hasRole(user, 'operator') ? 'Mulai dengan menekan "Jalankan Run Baru".' : 'Run yang dijalankan operator akan tampil di sini.'}
          </EmptyState>
        ) : (
          <>
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
                      onClick={() => navigate(`/runs/${r.id}`)}
                      className="cursor-pointer transition-colors hover:bg-gray-50"
                    >
                      <td className="whitespace-nowrap px-4 py-3">
                        <Link to={`/runs/${r.id}`} className="font-medium text-gray-800 hover:text-brand-600" onClick={(e) => e.stopPropagation()}>
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
                  <Link to={`/runs/${r.id}`} className="block space-y-3 px-4 py-4 active:bg-gray-50">
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
          </>
        )}
      </Card>
      {runs.length >= 100 && <p className="mt-3 text-center text-xs text-gray-500">Menampilkan 100 run terakhir.</p>}
    </>
  )
}
