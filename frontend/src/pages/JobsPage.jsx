import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { ChevronRight, Database, Plus, Search } from 'lucide-react'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDate } from '../lib/format'
import { TYPE_LABEL, jobType } from '../lib/jobs'
import { Alert, Badge, Button, Card, EmptyState, FreshnessBadge, GapBadge, LoadingBlock, PageHeader, inputClass } from '../components/ui'

// SATU tampilan untuk semua peran: nama tampilan, status, kesegaran, frekuensi. Identifier teknis (nama job) tampil kecil bila
// server mengirim blok `tech` (Operator/Admin); rincian teknis lain ada di halaman detail. Viewer hanya menerima data aktif.

const selectClass = `${inputClass} pr-8`
const NO_GROUP = '(tanpa kelompok)'

export function EnabledBadge({ enabled }) {
  return enabled
    ? <Badge tone="mint">Aktif</Badge>
    : <Badge tone="gray">Nonaktif</Badge>
}

export default function JobsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data, error, loading } = useApi('/jobs')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('enabled')
  const [groupFilter, setGroupFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const jobs = data?.jobs || []
  const technical = jobs.some((j) => j.tech)

  const groups = useMemo(() => [...new Set(jobs.map((j) => j.group || NO_GROUP))].sort(), [jobs])
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return jobs.filter((j) => {
      if (status === 'late') {
        if (!j.enabled || j.freshness?.status !== 'late') return false
      } else if (status === 'gaps') {
        if (!j.enabled || !j.freshness?.gaps?.count) return false
      } else if (status !== 'all' && j.enabled !== (status === 'enabled')) return false
      if (groupFilter !== 'all' && (j.group || NO_GROUP) !== groupFilter) return false
      if (typeFilter !== 'all' && jobType(j) !== typeFilter) return false
      if (!q) return true
      return [j.label, j.name, j.group, j.tech?.template, j.tech?.args?.table].some((v) => v && String(v).toLowerCase().includes(q))
    })
  }, [jobs, search, status, groupFilter, typeFilter])

  if (loading && !data) return <LoadingBlock />
  const enabledCount = jobs.filter((j) => j.enabled).length
  const open = (name) => navigate(`/jobs/${encodeURIComponent(name)}`)

  return (
    <>
      <PageHeader
        title="Daftar Data"
        description={technical ? `${enabledCount} data aktif dari ${jobs.length} terdaftar.` : `${enabledCount} data aktif yang masuk ke datalake.`}
        actions={hasRole(user, 'operator') && <Button icon={Plus} onClick={() => navigate('/jobs/new')}>Tambah Data</Button>}
      />
      {error && <Alert tone="error" className="mb-4">Daftar data belum bisa dimuat. Coba lagi beberapa saat lagi.</Alert>}

      <Card className="overflow-hidden">
        <div className="grid gap-3 border-b border-gray-200 p-4 sm:grid-cols-2 lg:grid-cols-[1fr_auto_auto_auto]">
          <div className="relative sm:col-span-2 lg:col-span-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
            <input type="search" aria-label="Cari data" placeholder={technical ? 'Cari nama data, identifier, tabel…' : 'Cari nama data…'} className={`${inputClass} pl-9`} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <select aria-label="Filter status" className={selectClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="enabled">Aktif</option>
            <option value="late">Aktif · belum terbaru</option>
            <option value="gaps">Aktif · belum lengkap</option>
            {technical && <option value="disabled">Nonaktif</option>}
            {technical && <option value="all">Semua status</option>}
          </select>
          <select aria-label="Filter kelompok" className={selectClass} value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
            <option value="all">Semua kelompok</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
          <select aria-label="Filter frekuensi" className={selectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="all">Semua frekuensi</option>
            <option value="daily">Harian</option>
            <option value="weekly">Mingguan</option>
            <option value="monthly">Bulanan</option>
            <option value="master">Master</option>
          </select>
        </div>

        {visible.length === 0 ? (
          <EmptyState icon={Database} title="Tidak ada data yang cocok">Ubah kata kunci atau filter.</EmptyState>
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/80 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-3">Data</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Kesegaran</th>
                    <th className="px-4 py-3">Data terakhir</th>
                    <th className="px-4 py-3">Frekuensi</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {visible.map((j) => (
                    <tr key={j.name} onClick={() => open(j.name)} className="cursor-pointer transition-colors hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <p className="font-medium text-gray-800">{j.label}</p>
                        {j.tech && <p className="mt-0.5 font-mono text-[11px] text-gray-400">{j.name}</p>}
                      </td>
                      <td className="px-4 py-3"><EnabledBadge enabled={j.enabled} /></td>
                      <td className="px-4 py-3">
                        {j.enabled ? (
                          <span className="flex flex-wrap gap-1.5"><FreshnessBadge freshness={j.freshness} /><GapBadge gaps={j.freshness?.gaps} /></span>
                        ) : <span className="text-gray-400">–</span>}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-600">{j.freshness?.latest ? fmtDate(j.freshness.latest) : '–'}</td>
                      <td className="px-4 py-3 text-gray-600">{TYPE_LABEL[jobType(j)]}</td>
                      <td className="pr-3 text-gray-300"><ChevronRight className="h-4 w-4" aria-hidden /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="divide-y divide-gray-100 md:hidden">
              {visible.map((j) => (
                <li key={j.name}>
                  <button type="button" onClick={() => open(j.name)} className="block w-full space-y-1.5 px-4 py-3.5 text-left active:bg-gray-50">
                    <div className="flex items-center justify-between gap-3">
                      <span className="min-w-0 truncate font-medium text-gray-800">{j.label}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {j.enabled && <FreshnessBadge freshness={j.freshness} />}
                        {j.enabled && <GapBadge gaps={j.freshness?.gaps} />}
                        <EnabledBadge enabled={j.enabled} />
                      </span>
                    </div>
                    <p className="text-xs text-gray-500">
                      {TYPE_LABEL[jobType(j)]}{j.freshness?.latest ? ` · data terakhir ${fmtDate(j.freshness.latest)}` : ''}
                      {j.tech && <span className="font-mono text-gray-400"> · {j.name}</span>}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <p className="mt-3 text-xs text-gray-500">Menampilkan {visible.length} data.</p>
    </>
  )
}
