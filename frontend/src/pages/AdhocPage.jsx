import { useMemo, useState } from 'react'
import { Database, Download, FileDown, Search, Table2 } from 'lucide-react'
import { apiDownload } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtBytes, fmtDate, fmtNumber, fmtRelative } from '../lib/format'
import { ColumnSelect, DbSelect, TablePicker } from '../components/schema'
import { Alert, Badge, Button, Card, CardHeader, Field, LoadingBlock, PageHeader, cx, inputClass } from '../components/ui'

function Tabs({ value, onChange, tabs }) {
  return (
    <div className="mb-6 inline-grid gap-1 rounded-lg bg-white p-1 shadow-card ring-1 ring-gray-200/80" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }} role="tablist">
      {tabs.map(([k, label, Icon]) => (
        <button
          key={k}
          type="button"
          role="tab"
          aria-selected={value === k}
          onClick={() => onChange(k)}
          className={cx('inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium transition-colors',
            value === k ? 'bg-brand-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800')}
        >
          <Icon className="h-4 w-4" aria-hidden /> {label}
        </button>
      ))}
    </div>
  )
}

function Result({ result }) {
  if (!result) return null
  if (result.error) return <Alert tone="error" title="Ekspor gagal">{result.error}</Alert>
  return (
    <Alert tone={result.truncated ? 'warning' : 'info'} title={`${result.name} terunduh`}>
      {fmtNumber(result.rows)} baris · {fmtBytes(result.size)} · {result.elapsed} dtk.
      {result.truncated && ` Data dipotong pada batas ${fmtNumber(result.maxRows)} baris — untuk data lengkap pakai run biasa (Jalankan).`}
    </Alert>
  )
}

function JobPicker({ jobs, value, onChange }) {
  const [q, setQ] = useState('')
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (s ? jobs.filter((j) => j.name.includes(s) || (j.table || '').toLowerCase().includes(s)) : jobs).slice(0, 150)
  }, [jobs, q])
  const selected = jobs.find((j) => j.name === value)
  if (selected) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg bg-brand-50 px-3 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-brand-900">{selected.name}</p>
          {selected.table && <p className="truncate font-mono text-xs text-brand-800/70">{selected.db}.dbo.{selected.table}</p>}
        </div>
        <button type="button" onClick={() => onChange(null)} className="shrink-0 text-xs font-medium text-brand-700 hover:underline">Ganti</button>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
        <input id="adhoc-job" type="search" className={`${inputClass} pl-9`} placeholder="Cari nama job atau tabel…" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
      </div>
      <ul className="max-h-72 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200">
        {shown.map((j) => (
          <li key={j.name}>
            <button type="button" onClick={() => onChange(j.name)} className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-gray-50">
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-gray-800">{j.name}</span>
                {j.table && <span className="block truncate font-mono text-xs text-gray-500">{j.db}.dbo.{j.table}</span>}
              </span>
              <Badge tone={j.needs_date ? 'mint' : 'blue'}>{j.needs_date ? 'Harian' : 'Master'}</Badge>
            </button>
          </li>
        ))}
        {shown.length === 0 && <li className="p-3 text-sm text-gray-500">Tidak ada job yang cocok.</li>}
      </ul>
    </div>
  )
}

export default function AdhocPage() {
  const { user } = useAuth()
  const isOperator = hasRole(user, 'operator')
  const { data: meta, error, loading, reload } = useApi('/adhoc/meta')
  const [tab, setTab] = useState('job')
  const [jobName, setJobName] = useState(null)
  const [date, setDate] = useState('')
  const [db, setDb] = useState()
  const [table, setTable] = useState()
  const [dateCol, setDateCol] = useState()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const { data: cols } = useApi(tab === 'table' && db && table ? `/schema/columns?db=${encodeURIComponent(db)}&table=${encodeURIComponent(table)}` : null)

  if (loading && !meta) return <LoadingBlock />
  if (meta && !meta.export_allowed) {
    return (
      <>
        <PageHeader title="Ekspor Cepat" description="Unduh data sebagai .parquet langsung dari SQL Server — tanpa membuat job dan tanpa menyentuh S3 datalake." />
        <Alert tone="warning" title="Menunggu persetujuan admin">
          Akun Anda masuk otomatis sebagai Viewer lewat SSO. Ekspor Cepat bisa dipakai setelah admin aplikasi menyetujuinya
          di halaman Users. Daftar job, riwayat run, dan log tetap bisa dilihat.
        </Alert>
      </>
    )
  }
  const job = meta?.jobs.find((j) => j.name === jobName)
  const theDate = date || meta?.default_date

  const download = async (path, body) => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await apiDownload(path, body))
      reload()
    } catch (e) {
      setResult({ error: e.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader title="Ekspor Cepat" description="Unduh data sebagai .parquet langsung dari SQL Server — tanpa membuat job dan tanpa menyentuh S3 datalake." />
      {error && <Alert tone="error" className="mb-4">{error.message}</Alert>}
      {isOperator && (
        <Tabs value={tab} onChange={(t) => { setTab(t); setResult(null) }} tabs={[['job', 'Dari job', Database], ['table', 'Tabel bebas', Table2]]} />
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          {tab === 'job' ? (
            <div className="space-y-5 p-5">
              <Field label="Job" htmlFor="adhoc-job" hint="Data yang bisa diunduh = data job yang terdaftar & aktif.">
                <JobPicker jobs={meta?.jobs || []} value={jobName} onChange={setJobName} />
              </Field>
              {job?.needs_date !== false && (
                <Field label="Tanggal data" htmlFor="adhoc-date">
                  <input id="adhoc-date" type="date" className={`${inputClass} max-w-52`} value={theDate || ''} onChange={(e) => setDate(e.target.value)} />
                </Field>
              )}
              {job && !job.needs_date && <p className="text-sm text-gray-500">Job master — seluruh isi tabel saat ini diunduh.</p>}
              <Button icon={Download} loading={busy} disabled={!job} onClick={() => download('/adhoc/job', { job_name: job.name, target_date: job.needs_date ? theDate : null })}>
                Download .parquet
              </Button>
              <Result result={result} />
            </div>
          ) : (
            <div className="space-y-5 p-5">
              <DbSelect id="adhoc-db" value={db} onChange={(v) => { setDb(v); setTable(undefined); setDateCol(undefined) }} />
              <TablePicker id="adhoc-table" db={db} value={table} onChange={(v) => { setTable(v); setDateCol(undefined) }} />
              {cols && (
                <ColumnSelect
                  id="adhoc-datecol"
                  label="Filter kolom tanggal (opsional)"
                  hint={`Kosongkan untuk mengambil seluruh isi tabel (±${fmtNumber(cols.rows)} baris, maks ${fmtNumber(meta.max_rows)}).`}
                  columns={cols.columns}
                  filter={(c) => c.date_kind === 'datetime'}
                  value={dateCol}
                  onChange={setDateCol}
                />
              )}
              {dateCol && (
                <Field label="Tanggal data" htmlFor="adhoc-tdate">
                  <input id="adhoc-tdate" type="date" className={`${inputClass} max-w-52`} value={theDate || ''} onChange={(e) => setDate(e.target.value)} />
                </Field>
              )}
              <Button icon={Download} loading={busy} disabled={!db || !table} onClick={() => download('/adhoc/table', { db, table, date_col: dateCol || null, target_date: dateCol ? theDate : null })}>
                Download .parquet
              </Button>
              <Result result={result} />
            </div>
          )}
        </Card>

        <div className="space-y-6 lg:col-span-2">
          <div className="rounded-xl border border-brand-100 bg-brand-50 p-5 text-sm leading-relaxed text-brand-900/80">
            <p className="flex items-center gap-2 font-semibold text-brand-900"><FileDown className="h-4 w-4 text-brand-600" aria-hidden /> Tentang ekspor cepat</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Maks <strong>{fmtNumber(meta?.max_rows)}</strong> baris per file — lebih dari itu dipotong.</li>
              <li>Skema sama dengan file di datalake (termasuk kolom <span className="font-mono text-xs">_ingested_at</span>).</li>
              <li>Setiap unduhan tercatat atas nama Anda.</li>
            </ul>
          </div>
          <Card className="overflow-hidden">
            <CardHeader title="Unduhan saya" description="15 terakhir" />
            {meta?.history.length ? (
              <ul className="divide-y divide-gray-100">
                {meta.history.map((h) => (
                  <li key={h.id} className="px-5 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-medium text-gray-800">{h.source}</span>
                      <Badge tone={h.status === 'success' ? (h.truncated ? 'amber' : 'mint') : 'gray'}>
                        {h.status === 'success' ? (h.truncated ? 'Terpotong' : 'Lengkap') : 'Gagal'}
                      </Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-gray-500">
                      {h.target_date ? fmtDate(h.target_date) : 'seluruh tabel'} · {fmtNumber(h.rows)} baris · {fmtRelative(h.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-6 text-sm text-gray-500">Belum ada unduhan.</p>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
