import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { ChevronRight, Database, Lock, Plus, Search } from 'lucide-react'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { NO_MODULE, TYPE_LABEL, jobType } from '../lib/jobs'
import { Alert, Badge, Button, Card, EmptyState, FreshnessBadge, GapBadge, LoadingBlock, PageHeader, inputClass } from '../components/ui'

const selectClass = `${inputClass} pr-8`

export function EnabledBadge({ enabled }) {
  return enabled
    ? <Badge tone="mint">Aktif</Badge>
    : <Badge tone="gray">Nonaktif</Badge>
}

export function Target({ job }) {
  if (!job.s3_folder) return <span className="text-gray-400">tanpa S3</span>
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-xs text-gray-600">
      {job.s3_folder}
      {job.s3_locked_at && (
        <Lock className="h-3 w-3 text-amber-500" aria-label="Path S3 terkunci" title="Path S3 sudah dipakai — terkunci" />
      )}
    </span>
  )
}

export default function JobsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data, error, loading } = useApi('/jobs')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('enabled')
  const [moduleFilter, setModuleFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const jobs = data?.jobs || []

  const modules = useMemo(() => [...new Set(jobs.map((j) => j.s3?.module || NO_MODULE))].sort(), [jobs])
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return jobs.filter((j) => {
      if (status === 'late') {
        if (!j.enabled || j.freshness?.status !== 'late') return false
      } else if (status === 'gaps') {
        if (!j.enabled || !j.freshness?.gaps?.count) return false
      } else if (status !== 'all' && j.enabled !== (status === 'enabled')) return false
      if (moduleFilter !== 'all' && (j.s3?.module || NO_MODULE) !== moduleFilter) return false
      if (typeFilter !== 'all' && jobType(j) !== typeFilter) return false
      if (!q) return true
      return [j.name, j.template, j.s3?.module, j.s3?.dataset, j.output, j.args?.table].some((v) => v && String(v).toLowerCase().includes(q))
    })
  }, [jobs, search, status, moduleFilter, typeFilter])

  if (loading && !data) return <LoadingBlock />
  const enabledCount = jobs.filter((j) => j.enabled).length
  const open = (name) => navigate(`/jobs/${encodeURIComponent(name)}`)

  return (
    <>
      <PageHeader
        title="Daftar Job"
        description={`${enabledCount} job aktif dari ${jobs.length} job terdaftar.`}
        actions={hasRole(user, 'operator') && <Button icon={Plus} onClick={() => navigate('/jobs/new')}>Tambah Job</Button>}
      />
      {error && <Alert tone="error" className="mb-4">{error.message}</Alert>}
      <Alert tone="info" className="mb-4">
        Job aktif yang namanya diawali <span className="font-mono">bronze_</span> otomatis ikut jadwal{' '}
        <span className="font-mono">run_exporter.bat</span>. Ikon <Lock className="inline h-3 w-3 text-amber-500" aria-hidden /> = path S3
        sudah dipakai produksi, hanya admin yang bisa memindahkannya.
      </Alert>

      <Card className="overflow-hidden">
        <div className="grid gap-3 border-b border-gray-200 p-4 sm:grid-cols-2 lg:grid-cols-[1fr_auto_auto_auto]">
          <div className="relative sm:col-span-2 lg:col-span-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
            <input type="search" aria-label="Cari job" placeholder="Cari nama job, tabel, modul, dataset…" className={`${inputClass} pl-9`} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <select aria-label="Filter status" className={selectClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="enabled">Aktif</option>
            <option value="late">Aktif · terlambat</option>
            <option value="gaps">Aktif · ada celah</option>
            <option value="disabled">Nonaktif</option>
            <option value="all">Semua status</option>
          </select>
          <select aria-label="Filter modul" className={selectClass} value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)}>
            <option value="all">Semua modul</option>
            {modules.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <select aria-label="Filter tipe" className={selectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="all">Semua tipe</option>
            <option value="daily">Harian</option>
            <option value="weekly">Mingguan</option>
            <option value="monthly">Bulanan</option>
            <option value="master">Master</option>
          </select>
        </div>

        {visible.length === 0 ? (
          <EmptyState icon={Database} title="Tidak ada job yang cocok">Ubah kata kunci atau filter.</EmptyState>
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/80 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-3">Job</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Kesegaran</th>
                    <th className="px-4 py-3">Tipe</th>
                    <th className="px-4 py-3">Folder S3</th>
                    <th className="px-4 py-3">Diubah</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {visible.map((j) => (
                    <tr key={j.name} onClick={() => open(j.name)} className="cursor-pointer transition-colors hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <p className="font-medium text-gray-800">{j.name}</p>
                        <p className="mt-0.5 max-w-md truncate text-xs text-gray-500">{j.template_label}</p>
                      </td>
                      <td className="px-4 py-3"><EnabledBadge enabled={j.enabled} /></td>
                      <td className="px-4 py-3">
                        {j.enabled ? (
                          <span className="flex flex-wrap gap-1.5"><FreshnessBadge freshness={j.freshness} /><GapBadge gaps={j.freshness?.gaps} /></span>
                        ) : <span className="text-gray-400">–</span>}
                      </td>
                      <td className="px-4 py-3 text-gray-600">{TYPE_LABEL[jobType(j)]}</td>
                      <td className="px-4 py-3"><Target job={j} /></td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">{j.updated_by || '–'}</td>
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
                      <span className="truncate font-medium text-gray-800">{j.name}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {j.enabled && <FreshnessBadge freshness={j.freshness} />}
                        {j.enabled && <GapBadge gaps={j.freshness?.gaps} />}
                        <EnabledBadge enabled={j.enabled} />
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                      <span>{TYPE_LABEL[jobType(j)]}</span>·<Target job={j} />
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <p className="mt-3 text-xs text-gray-500">Menampilkan {visible.length} job.</p>
    </>
  )
}
