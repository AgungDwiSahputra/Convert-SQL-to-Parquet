import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { ChevronDown, Database, Eye, Play, Search, X } from 'lucide-react'
import { api } from '../lib/api'
import { useApi } from '../lib/hooks'
import { fmtDate, fmtDateRange, fmtNumber } from '../lib/format'
import { Alert, Badge, Button, Card, EmptyState, Field, LoadingBlock, Modal, PageHeader, Toggle, inputClass } from '../components/ui'
import { NO_MODULE, TYPE_LABEL, jobType } from '../lib/jobs'

const selectClass = `${inputClass} pr-8`

function SectionTitle({ step, children }) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-800">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-[11px] font-semibold text-white">{step}</span>
      {children}
    </h2>
  )
}

function JobRow({ job, checked, onToggle }) {
  const t = jobType(job)
  return (
    <li>
      <label className={`flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors ${checked ? 'bg-brand-50/70' : 'hover:bg-gray-50'}`}>
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-brand-600 accent-brand-600 focus:ring-brand-500"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-gray-800">{job.name}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={t === 'master' ? 'blue' : 'mint'}>
              {TYPE_LABEL[t]}
            </Badge>
            {job.s3 && <Badge>{job.s3.layer} · {job.s3.module || NO_MODULE} / {job.s3.dataset}</Badge>}
            <span className="text-xs text-gray-400">{job.template}</span>
          </div>
        </div>
      </label>
    </li>
  )
}

function PreviewModal({ preview, onClose, onSubmit, submitting, error }) {
  const [ack, setAck] = useState(false)
  if (!preview) return null
  const needsAck = preview.s3_upload
  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title="Konfirmasi run"
      description={`${fmtNumber(preview.total_items)} eksekusi · ${preview.job_count} job · ${preview.date_count} tanggal`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>Kembali</Button>
          <Button icon={Play} loading={submitting} disabled={needsAck && !ack} onClick={onSubmit}>Antrikan Run</Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Alert tone="error">{error}</Alert>}
        {preview.warnings.map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
        {preview.s3_upload ? (
          <Alert tone="info" title="Partisi S3 di bawah akan DITIMPA">
            Bucket <span className="font-mono">{preview.s3_bucket}</span>. Setiap job menulis data penuh untuk tanggalnya, jadi file lama di
            path yang sama diganti dengan hasil run ini.
          </Alert>
        ) : (
          <Alert tone="info">Tidak ada upload S3 pada run ini — file hanya disimpan di server (folder <span className="font-mono">output/</span>).</Alert>
        )}

        <div className="overflow-hidden rounded-lg border border-gray-200">
          <div className="max-h-80 overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-gray-50">
                <tr className="text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                  <th className="px-3 py-2">Job</th>
                  <th className="px-3 py-2">Tanggal</th>
                  <th className="px-3 py-2">{preview.s3_upload ? 'Path S3' : 'File'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {preview.items.map((i) => (
                  <tr key={`${i.job_name}@${i.target_date}`}>
                    <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-700">{i.job_name}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-gray-600">{fmtDate(i.target_date)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-600">
                      {!preview.s3_upload ? i.output
                        : i.s3_key ? <span className="break-all">{i.s3_key}</span>
                          : i.s3_dynamic ? <span className="text-gray-400">ditentukan dari data (partisi mingguan)</span>
                            : <span className="text-gray-400">tidak diupload (job tanpa tujuan S3)</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.truncated && (
            <p className="border-t border-gray-200 px-3 py-2 text-xs text-gray-500">
              Menampilkan {fmtNumber(preview.items.length)} dari {fmtNumber(preview.total_items)} eksekusi.
            </p>
          )}
        </div>

        {needsAck && (
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-200 bg-amber-50/60 px-4 py-3 text-sm text-amber-900">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5 h-4 w-4 accent-brand-600" />
            Saya paham partisi S3 di atas akan ditimpa dengan hasil run ini.
          </label>
        )}
      </div>
    </Modal>
  )
}

export default function NewRunPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { data: jobsData, error: jobsError, loading } = useApi('/jobs')
  const { data: meta } = useApi('/runs/meta')
  const jobs = useMemo(() => (jobsData?.jobs || []).filter((j) => j.enabled), [jobsData])

  const [selected, setSelected] = useState(() => new Set((params.get('jobs') || '').split(',').filter(Boolean)))
  const [search, setSearch] = useState('')
  const [moduleFilter, setModuleFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [mode, setMode] = useState('single')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [options, setOptions] = useState(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(null) // 'preview' | 'submit'
  const [error, setError] = useState(null)
  const [submitError, setSubmitError] = useState(null)

  useEffect(() => {
    if (!meta || options) return
    setOptions(meta.default_options)
    setStartDate((d) => d || meta.default_date)
    setEndDate((d) => d || meta.default_date)
  }, [meta, options])

  const modules = useMemo(() => {
    const counts = {}
    for (const j of jobs) {
      const m = j.s3?.module || NO_MODULE
      counts[m] = (counts[m] || 0) + 1
    }
    return Object.entries(counts).sort(([a], [b]) => a.localeCompare(b))
  }, [jobs])

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return jobs.filter((j) => {
      if (moduleFilter !== 'all' && (j.s3?.module || NO_MODULE) !== moduleFilter) return false
      if (typeFilter !== 'all' && jobType(j) !== typeFilter) return false
      if (!q) return true
      return [j.name, j.template, j.s3?.module, j.s3?.dataset].some((v) => v && v.toLowerCase().includes(q))
    })
  }, [jobs, search, moduleFilter, typeFilter])

  const selectedJobs = jobs.filter((j) => selected.has(j.name))
  const end = mode === 'single' ? startDate : endDate
  const dateError = mode === 'range' && startDate && endDate && endDate < startDate ? 'Tanggal akhir harus sama atau setelah tanggal awal.' : null
  const canPreview = selectedJobs.length > 0 && startDate && end && !dateError && options

  const toggle = (name) => setSelected((s) => {
    const next = new Set(s)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    return next
  })
  const selectVisible = () => setSelected((s) => new Set([...s, ...visible.map((j) => j.name)]))
  const clearAll = () => setSelected(new Set())
  const setOpt = (k, v) => setOptions((o) => ({ ...o, [k]: v }))

  const body = () => ({ jobs: selectedJobs.map((j) => j.name), start_date: startDate, end_date: end, options })

  const openPreview = async () => {
    setBusy('preview')
    setError(null)
    setSubmitError(null)
    try {
      setPreview(await api('/runs/preview', { method: 'POST', body: body() }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const submit = async () => {
    setBusy('submit')
    setSubmitError(null)
    try {
      const r = await api('/runs', { method: 'POST', body: body() })
      navigate(`/runs/${r.id}`)
    } catch (e) {
      setSubmitError(e.message)
      setBusy(null)
    }
  }

  if (loading && !jobsData) return <LoadingBlock />

  return (
    <>
      <PageHeader title="Jalankan Run" description="Pilih job dan tanggal data, cek preview, lalu antrikan. Worker akan mengeksekusinya di server." />
      {jobsError && <Alert tone="error" className="mb-4">{jobsError.message}</Alert>}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* 1. Pilih job */}
        <Card className="overflow-hidden lg:col-span-2">
          <div className="space-y-4 border-b border-gray-200 p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <SectionTitle step={1}>Pilih job <span className="font-normal text-gray-500">({selectedJobs.length} dari {jobs.length} dipilih)</span></SectionTitle>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={selectVisible} disabled={!visible.length}>Pilih semua yang tampil</Button>
                <Button size="sm" variant="ghost" onClick={clearAll} disabled={!selected.size}>Kosongkan</Button>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
                <input
                  type="search"
                  aria-label="Cari job"
                  placeholder="Cari nama job, modul, dataset…"
                  className={`${inputClass} pl-9`}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <select aria-label="Filter modul" className={selectClass} value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)}>
                <option value="all">Semua modul</option>
                {modules.map(([m, n]) => <option key={m} value={m}>{m} ({n})</option>)}
              </select>
              <select aria-label="Filter tipe" className={selectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
                <option value="all">Semua tipe</option>
                <option value="daily">Harian</option>
                <option value="weekly">Mingguan</option>
                <option value="monthly">Bulanan</option>
                <option value="master">Master</option>
              </select>
            </div>
          </div>
          {visible.length === 0 ? (
            <EmptyState icon={Database} title="Tidak ada job yang cocok">Ubah kata kunci atau filter.</EmptyState>
          ) : (
            <ul className="max-h-[32rem] divide-y divide-gray-100 overflow-y-auto lg:max-h-[calc(100vh-17rem)]">
              {visible.map((j) => <JobRow key={j.name} job={j} checked={selected.has(j.name)} onToggle={() => toggle(j.name)} />)}
            </ul>
          )}
        </Card>

        {/* 2-3. Tanggal & opsi */}
        <div className="space-y-6 lg:sticky lg:top-8 lg:self-start">
          <Card className="space-y-4 p-4 sm:p-5">
            <SectionTitle step={2}>Tanggal data</SectionTitle>
            <div className="grid grid-cols-2 gap-1 rounded-lg bg-gray-100 p-1" role="radiogroup" aria-label="Mode tanggal">
              {[['single', 'Satu tanggal'], ['range', 'Rentang']].map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={mode === k}
                  onClick={() => setMode(k)}
                  className={`h-9 rounded-md text-sm font-medium transition-colors ${mode === k ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-600 hover:text-gray-800'}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className={mode === 'range' ? 'grid grid-cols-2 gap-3' : ''}>
              <Field label={mode === 'range' ? 'Dari' : 'Tanggal'} htmlFor="start">
                <input id="start" type="date" className={inputClass} value={startDate} max={mode === 'range' ? endDate || undefined : undefined} onChange={(e) => setStartDate(e.target.value)} />
              </Field>
              {mode === 'range' && (
                <Field label="Sampai" htmlFor="end">
                  <input id="end" type="date" className={inputClass} value={endDate} min={startDate || undefined} onChange={(e) => setEndDate(e.target.value)} />
                </Field>
              )}
            </div>
            {dateError && <p className="text-xs text-red-600">{dateError}</p>}
            <p className="text-xs text-gray-500">
              Job harian dijalankan per tanggal. Job mingguan/bulanan dijalankan sekali per minggu/bulan yang tersentuh rentang (menarik seluruh periode, berlabel awal periode). Job master dijalankan sekali dengan snapshot tanggal {mode === 'range' ? 'awal' : 'ini'}.
            </p>
          </Card>

          {options && (
            <Card className="space-y-4 p-4 sm:p-5">
              <SectionTitle step={3}>Opsi</SectionTitle>
              <Toggle
                id="opt-s3"
                label="Upload ke S3"
                description={meta?.s3_enabled ? 'Menimpa partisi S3 tanggal terkait.' : 'Kredensial S3 server belum lengkap — upload akan dilewati.'}
                checked={!options.skip_s3}
                onChange={(v) => setOpt('skip_s3', !v)}
              />
              <Toggle
                id="opt-local"
                label="Simpan file di server"
                description="Salinan .parquet di folder output/ server."
                checked={!options.skip_local}
                onChange={(v) => setOpt('skip_local', !v)}
              />
              <Toggle
                id="opt-stop"
                label="Berhenti saat ada yang gagal"
                description="Kalau mati, job lain tetap dijalankan."
                checked={options.stop_on_error}
                onChange={(v) => setOpt('stop_on_error', v)}
              />
              <button
                type="button"
                onClick={() => setShowAdvanced((s) => !s)}
                aria-expanded={showAdvanced}
                className="flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-gray-800"
              >
                <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} aria-hidden /> Lanjutan
              </button>
              {showAdvanced && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Kompresi" htmlFor="opt-comp">
                    <select id="opt-comp" className={selectClass} value={options.compression} onChange={(e) => setOpt('compression', e.target.value)}>
                      {(meta?.compression_options || []).map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </Field>
                  <Field label="Batch (baris)" htmlFor="opt-batch">
                    <input
                      id="opt-batch"
                      type="number"
                      min={1000}
                      max={1000000}
                      step={1000}
                      className={inputClass}
                      value={options.batch}
                      onChange={(e) => setOpt('batch', Number(e.target.value))}
                    />
                  </Field>
                </div>
              )}
            </Card>
          )}

          <Card className="space-y-4 p-4 sm:p-5">
            <div className="text-sm text-gray-600">
              <p className="font-medium text-gray-800">
                {selectedJobs.length} job · {startDate ? fmtDateRange(startDate, end || startDate) : '–'}
              </p>
              {selectedJobs.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedJobs.slice(0, 8).map((j) => (
                    <span key={j.name} className="inline-flex items-center gap-1 rounded-md bg-gray-100 py-0.5 pl-2 pr-1 text-xs text-gray-700">
                      {j.name}
                      <button type="button" onClick={() => toggle(j.name)} aria-label={`Hapus ${j.name}`} className="rounded p-0.5 hover:bg-gray-200">
                        <X className="h-3 w-3" aria-hidden />
                      </button>
                    </span>
                  ))}
                  {selectedJobs.length > 8 && <span className="px-1 text-xs text-gray-500">+{selectedJobs.length - 8} lainnya</span>}
                </div>
              )}
            </div>
            {error && <Alert tone="error">{error}</Alert>}
            <Button icon={Eye} className="w-full" disabled={!canPreview} loading={busy === 'preview'} onClick={openPreview}>
              Preview &amp; Jalankan
            </Button>
          </Card>
        </div>
      </div>

      <PreviewModal
        key={preview ? JSON.stringify(body()) : 'none'}
        preview={preview}
        onClose={() => busy !== 'submit' && setPreview(null)}
        onSubmit={submit}
        submitting={busy === 'submit'}
        error={submitError}
      />
    </>
  )
}
