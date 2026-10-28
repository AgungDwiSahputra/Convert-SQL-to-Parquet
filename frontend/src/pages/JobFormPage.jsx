import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, CheckCircle2, ChevronDown, CircleAlert, FlaskConical, Lock, Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi, useDebounced } from '../lib/hooks'
import { fmtDate, fmtNumber } from '../lib/format'
import {
  ADVANCED_TEMPLATES, CADENCE_LABEL, DAILY_TEMPLATES, bronzeMode, pickArgs, suggestDataset, suggestModule, suggestName,
} from '../lib/jobs'
import { Alert, Badge, Button, Card, Field, LoadingBlock, Spinner, Toggle, inputClass } from '../components/ui'
import { ColumnSelect, DbSelect, Err, TablePicker } from '../components/schema'

const selectClass = `${inputClass} pr-8`
const enc = encodeURIComponent
// Error yang tidak menghalangi uji coba (baru berlaku saat simpan) -- sama dengan backend /test-run.
const NON_BLOCKING_FOR_TEST = ['name', 's3', 'output', 'dataset']

function clean(obj) {
  const out = {}
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = v
  return out
}

function Step({ n, title, description, children }) {
  return (
    <Card className="p-4 sm:p-5">
      <div className="mb-4 flex items-start gap-3">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">{n}</span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
        </div>
      </div>
      <div className="space-y-4">{children}</div>
    </Card>
  )
}

function TextInput({ id, label, value, onChange, error, hint, placeholder, mono = true, disabled, list }) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <input
        id={id}
        className={`${inputClass} ${mono ? 'font-mono' : ''} disabled:opacity-60`}
        value={value ?? ''}
        placeholder={placeholder}
        disabled={disabled}
        list={list}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
      <Err>{error}</Err>
    </Field>
  )
}

function ParamsEditor({ value, onChange, error }) {
  const params = value || []
  const kindOf = (p) => (p && typeof p === 'object' ? p.$var : typeof p === 'number' ? 'number' : 'text')
  const set = (i, p) => onChange(params.map((x, j) => (j === i ? p : x)))
  return (
    <Field label="Parameter (urutan sesuai tanda ? di query)">
      <div className="space-y-2">
        {params.map((p, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <div key={i} className="flex items-center gap-2">
            {/* lebar tetap lewat pembungkus: inputClass membawa w-full yang menang atas w-* di elemen yang sama */}
            <div className="w-44 shrink-0">
              <select
                aria-label={`Jenis parameter ${i + 1}`}
                className={selectClass}
                value={kindOf(p)}
                onChange={(e) => {
                  const k = e.target.value
                  set(i, k === 'target_date' || k === 'next_date' ? { $var: k } : k === 'number' ? 0 : '')
                }}
              >
                <option value="target_date">Tanggal proses</option>
                <option value="next_date">Tanggal + 1 hari</option>
                <option value="text">Teks</option>
                <option value="number">Angka</option>
              </select>
            </div>
            {typeof p === 'object' && p ? (
              <span className="flex h-11 flex-1 items-center rounded-lg bg-gray-100 px-3 text-xs text-gray-500 sm:h-10">diisi otomatis per tanggal</span>
            ) : (
              <input
                aria-label={`Nilai parameter ${i + 1}`}
                className={`${inputClass} font-mono`}
                type={typeof p === 'number' ? 'number' : 'text'}
                value={p ?? ''}
                onChange={(e) => set(i, typeof p === 'number' ? Number(e.target.value) : e.target.value)}
              />
            )}
            <Button variant="ghost" size="sm" icon={Trash2} aria-label="Hapus parameter" onClick={() => onChange(params.filter((_, j) => j !== i))} />
          </div>
        ))}
        <Button variant="secondary" size="sm" icon={Plus} onClick={() => onChange([...params, { $var: 'target_date' }])}>Tambah parameter</Button>
      </div>
      <Err>{error}</Err>
    </Field>
  )
}

function S3SpecEditor({ value, onChange, errors, modules }) {
  const spec = value || null
  const set = (k, v) => onChange({ ...spec, [k]: v === '' ? (k === 'module' ? null : v) : v })
  return (
    <div className="space-y-4">
      <Toggle
        id="s3-on"
        label="Upload ke S3"
        description="Matikan kalau job ini hanya menyimpan file di server."
        checked={!!spec}
        onChange={(on) => onChange(on ? { layout: 'daily', layer: 'gold', module: null, dataset: '', file_name: 'part-00001.parquet' } : null)}
      />
      {spec && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Layout partisi" htmlFor="s3-layout">
            <select id="s3-layout" className={selectClass} value={spec.layout} onChange={(e) => set('layout', e.target.value)}>
              <option value="daily">Harian — recorded_year/month/day</option>
              <option value="weekly">Mingguan — kolom week_* dari data</option>
              <option value="master">Master — snapshot_date</option>
            </select>
            <Err>{errors['s3.layout']}</Err>
          </Field>
          <TextInput id="s3-layer" label="Layer" value={spec.layer} onChange={(v) => set('layer', v)} error={errors['s3.layer']} placeholder="gold" />
          <TextInput id="s3-module" label="Modul (opsional)" value={spec.module} onChange={(v) => set('module', v)} error={errors['s3.module']} list="known-modules" />
          <TextInput id="s3-dataset" label="Dataset" value={spec.dataset} onChange={(v) => set('dataset', v)} error={errors['s3.dataset']} />
          <TextInput id="s3-file" label="Nama file di S3" value={spec.file_name} onChange={(v) => set('file_name', v)} error={errors['s3.file_name']} hint="Konstan antar-run (README §5 poin 3)." />
          <datalist id="known-modules">{modules.map((m) => <option key={m} value={m} />)}</datalist>
        </div>
      )}
      <Err>{errors.s3}</Err>
    </div>
  )
}

function TestResult({ result, stale, overrides, overrideTypes, onOverride, canOverride, needsDate }) {
  return (
    <div className="space-y-4">
      {stale && <Alert tone="warning">Isian job berubah sejak uji coba ini — jalankan ulang untuk hasil terbaru.</Alert>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 font-medium text-brand-700">
          <CheckCircle2 className="h-4 w-4" aria-hidden /> Query berhasil
        </span>
        <span className="text-gray-600">
          {fmtNumber(result.rows)} baris{result.limit_hit ? ` (batas ${result.limit} tercapai — data sebenarnya lebih banyak)` : ''}
        </span>
        <span className="text-gray-500">{result.elapsed_s} dtk</span>
        <span className="text-gray-500">tanggal {fmtDate(result.target_date)}</span>
      </div>
      {result.rows === 0 && (
        <Alert tone="warning">
          {needsDate
            ? 'Tidak ada data untuk tanggal ini. Coba tanggal lain — kalau selalu 0, cek kembali kolom/format tanggal.'
            : 'Query tidak mengembalikan baris.'}
        </Alert>
      )}
      {result.columns.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-gray-200">
          <div className="max-h-72 overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-gray-50">
                <tr className="text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                  <th className="px-3 py-2">Kolom</th>
                  <th className="px-3 py-2">Tipe Parquet</th>
                  {canOverride && <th className="px-3 py-2">Paksa tipe</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {result.columns.filter((c) => c.name !== '_ingested_at').map((c) => (
                  <tr key={c.name}>
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-xs text-gray-700">{c.name}</td>
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-xs text-gray-500">
                      {c.arrow_type}{c.overridden && <Badge tone="amber" className="ml-2">override</Badge>}
                    </td>
                    {canOverride && (
                      <td className="px-3 py-1">
                        <select aria-label={`Paksa tipe kolom ${c.name}`} className={`${selectClass} py-1 text-xs sm:py-1`} value={overrides?.[c.name] || ''} onChange={(e) => onOverride(c.name, e.target.value)}>
                          <option value="">(otomatis)</option>
                          {overrideTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {result.sample.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-gray-500">Contoh data ({Math.min(result.sample.length, 10)} baris pertama)</p>
          <div className="max-h-72 overflow-auto rounded-lg border border-gray-200">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 bg-gray-50">
                <tr>{result.columns.map((c) => <th key={c.name} className="whitespace-nowrap px-3 py-2 text-left font-mono font-medium text-gray-500">{c.name}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-gray-100 font-mono">
                {result.sample.slice(0, 10).map((row, i) => (
                  // eslint-disable-next-line react/no-array-index-key
                  <tr key={i}>
                    {row.map((v, j) => (
                      // eslint-disable-next-line react/no-array-index-key
                      <td key={j} className="max-w-56 truncate whitespace-nowrap px-3 py-1.5 text-gray-700" title={v === null ? 'NULL' : String(v)}>
                        {v === null ? <span className="text-gray-400">NULL</span> : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

export default function JobFormPage() {
  const { name: editName } = useParams()
  const isEdit = !!editName
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = hasRole(user, 'admin')

  const { data: meta } = useApi('/jobs/templates')
  const { data: runMeta } = useApi('/runs/meta')
  const { data: jobsData } = useApi('/jobs')
  const { data: existing, error: loadError } = useApi(isEdit ? `/jobs/${enc(editName)}` : null)

  const [draft, setDraft] = useState(isEdit ? null : { name: '', template: 'bronze_daily', args: {}, notes: '', cadence: '' })
  const [touched, setTouched] = useState({})
  const [attempted, setAttempted] = useState(false)
  const [detect, setDetect] = useState(null)
  const [validation, setValidation] = useState(null)
  const [validating, setValidating] = useState(false)
  const [testDate, setTestDate] = useState('')
  const [testLimit, setTestLimit] = useState(100)
  const [test, setTest] = useState(null) // {loading, result, error, key}
  const [enableNow, setEnableNow] = useState(false)
  const [confirmMove, setConfirmMove] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [showQuery, setShowQuery] = useState(false)

  // Edit: isi form dari job yang ada (sekali).
  useEffect(() => {
    if (isEdit && existing && !draft) {
      setDraft({ name: existing.name, template: existing.template, args: existing.args, notes: existing.notes || '', cadence: existing.cadence || '' })
    }
  }, [isEdit, existing, draft])
  useEffect(() => {
    if (runMeta && !testDate) setTestDate(runMeta.default_date)
  }, [runMeta, testDate])

  const tpl = draft?.template
  const args = useMemo(() => draft?.args || {}, [draft])
  const mode = bronzeMode(tpl)
  const isAdvanced = ADVANCED_TEMPLATES.includes(tpl)
  const errors = validation?.errors || {}
  const preview = validation?.preview
  const modules = useMemo(() => [...new Set((jobsData?.jobs || []).map((j) => j.s3?.module).filter(Boolean))].sort(), [jobsData])
  const templateLabel = (k) => meta?.templates.find((t) => t.key === k)?.label || k

  const setArgs = (patch) => setDraft((d) => ({ ...d, args: clean({ ...d.args, ...patch }) }))
  const setTemplate = (t, patch = {}) => setDraft((d) => ({ ...d, template: t, args: pickArgs(clean({ ...d.args, ...patch }), t) }))
  const touch = (k) => setTouched((t) => ({ ...t, [k]: true }))

  // Kolom tabel utama & tabel induk (JOIN)
  const { data: colsData } = useApi(args.db && args.table ? `/schema/columns?db=${enc(args.db)}&table=${enc(args.table)}` : null)
  const { data: parentCols } = useApi(mode === 'join' && args.join_db && args.join_table
    ? `/schema/columns?db=${enc(args.join_db)}&table=${enc(args.join_table)}` : null)
  const columns = colsData?.columns || []

  // Saran otomatis modul/dataset/nama/file (hanya saat membuat job dari tabel).
  useEffect(() => {
    if (isEdit || !draft || isAdvanced || !args.table) return
    const module = touched.module ? args.module : suggestModule(args.table) || null
    const dataset = touched.dataset ? args.dataset : suggestDataset(args.table, module)
    const name = touched.name ? draft.name : suggestName(module, dataset)
    const output = touched.output ? args.output : name ? `${name}.parquet` : undefined
    if (module !== args.module || dataset !== args.dataset || name !== draft.name || output !== args.output) {
      setDraft((d) => ({ ...d, name, args: clean({ ...d.args, module, dataset, output }) }))
    }
  }, [isEdit, draft, isAdvanced, args.table, args.module, args.dataset, args.output, touched])
  useEffect(() => {
    if (isEdit || !draft || !isAdvanced || touched.output) return
    const output = draft.name ? `${draft.name}.parquet` : undefined
    if (output !== args.output) setDraft((d) => ({ ...d, args: clean({ ...d.args, output }) }))
  }, [isEdit, draft, isAdvanced, args.output, touched.output])

  // Validasi live (debounce)
  const payload = useMemo(() => (draft ? {
    name: draft.name, template: draft.template, args: draft.args, original_name: isEdit ? editName : null, confirm_s3_move: confirmMove,
  } : null), [draft, isEdit, editName, confirmMove])
  const debounced = useDebounced(payload, 450)
  useEffect(() => {
    if (!debounced) return undefined
    let stale = false
    setValidating(true)
    api('/jobs/validate', { method: 'POST', body: debounced })
      .then((v) => !stale && setValidation(v))
      .catch((e) => !stale && setValidation({ ok: false, errors: { _: e.message }, warnings: [] }))
      .finally(() => !stale && setValidating(false))
    return () => { stale = true }
  }, [debounced])

  const currentKey = JSON.stringify({ t: tpl, a: args })
  const testedOk = test?.result && test.key === currentKey
  const hasValue = (f) => (f === 'name' ? !!draft?.name : ![undefined, null, ''].includes(args[f]))
  const fieldError = (f) => (errors[f] && (attempted || errors[f] !== 'Wajib diisi.' || hasValue(f)) ? errors[f] : null)
  const blockingForTest = Object.keys(errors).filter((k) => !NON_BLOCKING_FOR_TEST.includes(k))
  const needsDate = preview ? preview.needs_date : mode === 'daily' || mode === 'join' || !!args.needs_date
  const dateDetectBlocks = mode === 'daily' && detect && detect.ok === false && !detect.loading

  if (isEdit && loadError) {
    return (
      <div className="space-y-4">
        <Alert tone="error">{loadError.message}</Alert>
        <Link to="/jobs" className="text-sm font-medium text-brand-600 hover:underline">← Kembali ke daftar job</Link>
      </div>
    )
  }
  if (!draft || !meta) return <LoadingBlock />
  if (isEdit && existing && !existing.can_edit) {
    return <Alert tone="error">Job ini memakai template khusus admin — hanya admin yang bisa mengubahnya.</Alert>
  }

  const pickDateCol = async (colName) => {
    const col = columns.find((c) => c.name === colName)
    if (!col) {
      setArgs({ date_col: undefined })
      setDetect(null)
      return
    }
    if (col.date_kind === 'datetime') {
      setTemplate('bronze_daily', { date_col: colName })
      setDetect({ ok: true, message: `Tipe ${col.type} — difilter rentang 00:00–24:00 per hari.` })
      return
    }
    setArgs({ date_col: colName })
    setDetect({ loading: true })
    try {
      const r = await api(`/schema/sample?db=${enc(args.db)}&table=${enc(args.table)}&column=${enc(colName)}`)
      const fallback = col.date_kind === 'epoch' ? 'bronze_daily_epoch_ms' : 'bronze_daily_strdate'
      setTemplate(r.detected.template || fallback, { date_col: colName, date_format: r.detected.date_format || undefined })
      setDetect({ ...r.detected, values: r.values })
    } catch (e) {
      setDetect({ ok: false, message: e.message })
    }
  }

  const setMode = (m) => {
    setDetect(null)
    if (m === 'daily') setTemplate(DAILY_TEMPLATES.includes(tpl) ? tpl : 'bronze_daily')
    if (m === 'master') setTemplate('bronze_master')
    if (m === 'join') setTemplate('bronze_daily_join', { join_db: args.join_db || args.db, alias: args.alias || 'c', join_alias: args.join_alias || 'p', date_col: undefined })
  }

  // JOIN: join_on = "<alias>.<kolom anak> = <alias induk>.<kolom induk>"
  const joinMatch = /^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$/.exec(args.join_on || '')
  const joinMap = joinMatch ? { [joinMatch[1]]: joinMatch[2], [joinMatch[3]]: joinMatch[4] } : {}
  const joinChild = joinMap[args.alias] || ''
  const joinParent = joinMap[args.join_alias] || ''
  const setJoin = ({ child = joinChild, parent = joinParent, alias = args.alias, joinAlias = args.join_alias }) => setArgs({
    alias, join_alias: joinAlias, join_on: child && parent ? `${alias}.${child} = ${joinAlias}.${parent}` : undefined,
  })

  const runTest = async () => {
    setTest({ loading: true, key: currentKey })
    try {
      const result = await api('/jobs/test-run', {
        method: 'POST',
        body: { name: draft.name, template: tpl, args, original_name: isEdit ? editName : null, target_date: testDate || null, limit: testLimit },
      })
      setTest({ result, key: currentKey })
    } catch (e) {
      setTest({ error: e.message, key: currentKey })
    }
  }

  const setOverride = (col, type) => {
    const next = { ...(args.overrides || {}) }
    if (type) next[col] = type
    else delete next[col]
    setArgs({ overrides: Object.keys(next).length ? next : undefined })
  }

  const save = async () => {
    setAttempted(true)
    setSaving(true)
    setSaveError(null)
    try {
      if (isEdit) {
        await api(`/jobs/${enc(editName)}`, { method: 'PUT', body: { template: tpl, args, notes: draft.notes, cadence: draft.cadence || null, confirm_s3_move: confirmMove } })
        navigate(`/jobs/${enc(editName)}`)
      } else {
        await api('/jobs', { method: 'POST', body: { name: draft.name, template: tpl, args, notes: draft.notes, cadence: draft.cadence || null, enabled: enableNow && !!testedOk } })
        navigate(`/jobs/${enc(draft.name)}`)
      }
    } catch (e) {
      setSaveError(e.message)
      if (e.detail?.errors) setValidation((v) => ({ ...(v || {}), ok: false, errors: e.detail.errors, needs_s3_confirm: e.detail.needs_s3_confirm }))
      setSaving(false)
    }
  }

  const errorList = Object.entries(errors).filter(([f, m]) => attempted || m !== 'Wajib diisi.' || hasValue(f))
  const missingCount = Object.values(errors).filter((m) => m === 'Wajib diisi.').length

  return (
    <>
      <div className="mb-6">
        <Link to={isEdit ? `/jobs/${enc(editName)}` : '/jobs'} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" aria-hidden /> {isEdit ? editName : 'Daftar Job'}
        </Link>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-gray-800">{isEdit ? `Edit ${editName}` : 'Tambah Job'}</h1>
        <p className="mt-1 text-sm text-gray-500">
          {isEdit ? 'Perubahan berlaku untuk run berikutnya (Web UI & jadwal CLI). Semua perubahan tercatat di riwayat.'
            : 'Pilih tabel sumber, cek hasilnya dengan uji coba, lalu simpan. Job baru tersimpan nonaktif kecuali uji coba berhasil dan Anda mengaktifkannya.'}
        </p>
      </div>

      {isEdit && existing?.s3_locked_at && (
        <Alert tone="warning" className="mb-6" title="Path S3 terkunci">
          Path S3 job ini sudah dipakai sejak {fmtDate(existing.s3_locked_at.slice(0, 10))}. Perubahan modul, dataset, nama file, atau jenis
          data yang memindahkan path S3 {isAdmin ? 'butuh konfirmasi Anda — data di path lama tetap tertinggal di S3.' : 'hanya bisa dilakukan admin.'}
        </Alert>
      )}

      {!isEdit && isAdmin && (
        <div className="mb-6 grid max-w-md grid-cols-2 gap-1 rounded-lg bg-gray-100 p-1" role="radiogroup" aria-label="Jenis form">
          {[['bronze', 'Dari tabel (wizard)'], ['advanced', 'Lanjutan (admin)']].map(([k, label]) => {
            const active = k === 'advanced' ? isAdvanced : !isAdvanced
            return (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => {
                  setDetect(null)
                  setTest(null)
                  setDraft((d) => ({ ...d, template: k === 'advanced' ? 'sp_job' : 'bronze_daily', args: {} }))
                  setTouched({})
                }}
                className={`h-9 rounded-md text-sm font-medium transition-colors ${active ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-600 hover:text-gray-800'}`}
              >
                {label}
              </button>
            )
          })}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {!isAdvanced && (
            <>
              <Step n={1} title="Sumber data" description="Tabel di SQL Server yang akan disalin mentah (bronze) ke Parquet.">
                <DbSelect id="db" value={args.db} error={fieldError('db')} onChange={(db) => { setDetect(null); setArgs({ db, table: undefined, date_col: undefined }) }} />
                <TablePicker id="table" db={args.db} value={args.table} error={fieldError('table')} onChange={(table) => { setDetect(null); setArgs({ table, date_col: undefined }) }} />
                {colsData && (
                  <p className="text-xs text-gray-500">
                    {columns.length} kolom{colsData.rows !== null ? ` · ±${fmtNumber(colsData.rows)} baris` : ' · view'}
                  </p>
                )}
              </Step>

              <Step n={2} title="Jenis data" description="Menentukan apakah data diambil per tanggal atau disalin penuh setiap run.">
                <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Jenis data">
                  {[
                    ['daily', 'Harian', 'Difilter per tanggal lewat kolom tanggal tabel ini.'],
                    ['master', 'Master', 'Snapshot penuh setiap run (tabel referensi/kecil).'],
                    ['join', 'Harian via JOIN', 'Tabel tanpa kolom tanggal — filter lewat tabel induk.'],
                  ].map(([k, label, desc]) => (
                    <button
                      key={k}
                      type="button"
                      role="radio"
                      aria-checked={mode === k}
                      onClick={() => setMode(k)}
                      className={`rounded-lg border px-3 py-3 text-left transition-colors ${mode === k
                        ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-500'
                        : 'border-gray-200 hover:border-gray-300'}`}
                    >
                      <p className="text-sm font-medium text-gray-800">{label}</p>
                      <p className="mt-0.5 text-xs text-gray-500">{desc}</p>
                    </button>
                  ))}
                </div>

                {mode === 'daily' && (
                  <>
                    <ColumnSelect
                      id="date_col"
                      label="Kolom tanggal"
                      columns={columns}
                      filter={(c) => c.date_kind}
                      value={args.date_col}
                      onChange={pickDateCol}
                      error={fieldError('date_col')}
                      hint={args.table ? 'Hanya kolom bertipe tanggal/jam, teks, atau bigint yang ditampilkan.' : 'Pilih tabel dulu.'}
                    />
                    {detect?.loading && <p className="flex items-center gap-2 text-sm text-gray-500"><Spinner /> Mengecek format isi kolom…</p>}
                    {detect && !detect.loading && (
                      <Alert tone={detect.ok ? 'info' : 'error'} title={detect.ok ? templateLabel(tpl) : 'Kolom ini tidak bisa dipakai'}>
                        {detect.message}
                      </Alert>
                    )}
                    {tpl === 'bronze_daily_strdate_eq' && (
                      <Field label="Format tanggal di kolom" htmlFor="date_format">
                        <select id="date_format" className={selectClass} value={args.date_format || ''} onChange={(e) => setArgs({ date_format: e.target.value || undefined })}>
                          <option value="">— pilih format —</option>
                          {meta.strdate_formats.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                        </select>
                        <Err>{fieldError('date_format')}</Err>
                      </Field>
                    )}
                  </>
                )}

                {mode === 'master' && colsData?.rows > 1_000_000 && (
                  <Alert tone="warning">Tabel ini ±{fmtNumber(colsData.rows)} baris dan akan ditarik PENUH setiap run.</Alert>
                )}

                {mode === 'join' && (
                  <div className="space-y-4 rounded-lg border border-gray-200 p-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      <DbSelect id="join_db" label="Database tabel induk" value={args.join_db} error={fieldError('join_db')} onChange={(v) => setArgs({ join_db: v, join_table: undefined, join_on: undefined, date_col: undefined })} />
                    </div>
                    <TablePicker id="join_table" label="Tabel induk (punya kolom tanggal)" db={args.join_db} value={args.join_table} error={fieldError('join_table')} onChange={(v) => setArgs({ join_table: v, join_on: undefined, date_col: undefined })} />
                    <div className="grid gap-4 sm:grid-cols-2">
                      <ColumnSelect id="join_child" label={`Kolom penghubung di ${args.table || 'tabel utama'}`} columns={columns} value={joinChild} onChange={(v) => setJoin({ child: v })} />
                      <ColumnSelect id="join_parent" label={`Kolom penghubung di ${args.join_table || 'tabel induk'}`} columns={parentCols?.columns || []} value={joinParent} onChange={(v) => setJoin({ parent: v })} />
                    </div>
                    <Err>{fieldError('join_on')}</Err>
                    <ColumnSelect id="join_date" label="Kolom tanggal di tabel induk" columns={parentCols?.columns || []} filter={(c) => c.date_kind === 'datetime'} value={args.date_col} onChange={(v) => setArgs({ date_col: v })} error={fieldError('date_col')} />
                    <div className="grid grid-cols-2 gap-4 sm:max-w-xs">
                      <TextInput id="alias" label="Alias utama" value={args.alias} onChange={(v) => setJoin({ alias: v })} error={fieldError('alias')} />
                      <TextInput id="join_alias" label="Alias induk" value={args.join_alias} onChange={(v) => setJoin({ joinAlias: v })} error={fieldError('join_alias')} />
                    </div>
                    <p className="text-xs text-gray-500">Kolom yang diambil tetap hanya milik tabel utama ({args.alias}.*); tabel induk hanya dipakai untuk menyaring tanggal.</p>
                  </div>
                )}
              </Step>
            </>
          )}

          {isAdvanced && (
            <Step n={1} title="Definisi job (admin)" description="Template khusus admin — bisa menulis ke SQL Server (SP), memakai linked server, atau SQL bebas.">
              {!isEdit && (
                <Field label="Template" htmlFor="adv-template">
                  <select id="adv-template" className={selectClass} value={tpl} onChange={(e) => { setTemplate(e.target.value); setTest(null) }}>
                    {ADVANCED_TEMPLATES.map((t) => <option key={t} value={t}>{templateLabel(t)}</option>)}
                  </select>
                </Field>
              )}
              {tpl === 'sp_job' && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextInput id="sp" label="Nama SP" value={args.sp} onChange={(v) => setArgs({ sp: v })} error={fieldError('sp')} placeholder="dbo.Nama_SP" />
                  <Field label="@function" htmlFor="function">
                    <input id="function" type="number" min={0} className={inputClass} value={args.function ?? ''} onChange={(e) => setArgs({ function: e.target.value === '' ? undefined : Number(e.target.value) })} />
                    <Err>{fieldError('function')}</Err>
                  </Field>
                  <div className="sm:col-span-2">
                    <Toggle id="needs_date" label="Kirim @TargetDate" description="Mati = job master (tidak tergantung tanggal)." checked={args.needs_date !== false} onChange={(v) => setArgs({ needs_date: v })} />
                  </div>
                  <Alert tone="warning" className="sm:col-span-2">SP dispatcher menjalankan ETL Load (MENULIS ke SQL Server) sebelum SELECT akhir. Uji coba tetap di-rollback, tapi membebani server.</Alert>
                </div>
              )}
              {tpl === 'custom_sql' && (
                <>
                  <Field label="Query SQL" htmlFor="query" hint="Placeholder parameter memakai tanda ? (bukan @param). Awali dengan SET NOCOUNT ON;">
                    <textarea id="query" rows={8} className={`${inputClass} font-mono text-xs`} value={args.query || ''} onChange={(e) => setArgs({ query: e.target.value })} />
                    <Err>{fieldError('query')}</Err>
                  </Field>
                  <ParamsEditor value={args.params} onChange={(v) => setArgs({ params: v.length ? v : undefined })} error={fieldError('params')} />
                  <Toggle id="needs_date_sql" label="Tergantung tanggal" description="Mati = dijalankan sekali saja di mode rentang (seperti master)." checked={args.needs_date !== false} onChange={(v) => setArgs({ needs_date: v })} />
                </>
              )}
              {tpl === 'bronze_arcsde_view' && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextInput id="linked_server" label="Linked server" value={args.linked_server} onChange={(v) => setArgs({ linked_server: v })} error={fieldError('linked_server')} />
                  <TextInput id="sde_db" label="Database SDE" value={args.sde_db} onChange={(v) => setArgs({ sde_db: v })} error={fieldError('sde_db')} />
                  <TextInput id="sde_version" label="Versi ArcSDE" value={args.sde_version} onChange={(v) => setArgs({ sde_version: v })} error={fieldError('sde_version')} />
                  <TextInput id="view" label="View (*_evw)" value={args.view} onChange={(v) => setArgs({ view: v })} error={fieldError('view')} />
                  <Field label="Kolom (satu per baris)" htmlFor="columns" hint="Jangan sertakan kolom geometry (Shape) — tidak didukung ODBC Driver 17.">
                    <textarea
                      id="columns"
                      rows={8}
                      className={`${inputClass} font-mono text-xs`}
                      value={(args.columns || []).join('\n')}
                      onChange={(e) => setArgs({ columns: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
                    />
                    <Err>{fieldError('columns')}</Err>
                  </Field>
                </div>
              )}
            </Step>
          )}

          <Step n={isAdvanced ? 2 : 3} title="Tujuan & nama" description="Lokasi file di S3 (Hive-style) dan nama job. Preview path ada di ringkasan.">
            {isAdvanced && tpl !== 'bronze_arcsde_view' ? (
              <S3SpecEditor value={args.s3} onChange={(v) => setArgs({ s3: v || undefined })} errors={Object.fromEntries(Object.keys(errors).map((k) => [k, fieldError(k)]))} modules={modules} />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <TextInput id="module" label="Modul (segmen source di S3)" value={args.module} list="known-modules-main" error={fieldError('module')} hint="mis. hms, iot, pzo — pilih yang sudah ada bila cocok."
                  onChange={(v) => { touch('module'); setArgs({ module: v || null }) }} />
                <TextInput id="dataset" label="Dataset" value={args.dataset} error={fieldError('dataset')} hint={mode === 'master' || tpl === 'bronze_arcsde_view' ? 'Folder otomatis diberi awalan master_.' : undefined}
                  onChange={(v) => { touch('dataset'); setArgs({ dataset: v }) }} />
                <datalist id="known-modules-main">{modules.map((m) => <option key={m} value={m} />)}</datalist>
              </div>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput id="name" label="Nama job" value={draft.name} disabled={isEdit} error={isEdit ? null : fieldError('name')}
                hint={isEdit ? 'Nama tidak bisa diubah (dipakai riwayat run & --only).' : 'Huruf kecil, angka, underscore. Awalan bronze_ = ikut jadwal CLI.'}
                onChange={(v) => { touch('name'); setDraft((d) => ({ ...d, name: v.trim() })) }} />
              <TextInput id="output" label="File output lokal" value={args.output} error={fieldError('output')}
                hint={!isAdvanced ? 'Untuk job bronze, juga dipakai sebagai nama file di S3.' : undefined}
                onChange={(v) => { touch('output'); setArgs({ output: v }) }} />
              {isEdit && existing?.args?.s3_file_name && mode === 'master' && (
                <TextInput id="s3_file_name" label="Nama file di S3 (khusus)" value={args.s3_file_name} error={fieldError('s3_file_name')}
                  hint="Dipertahankan dari job lama — mengubahnya memindahkan path S3." onChange={(v) => setArgs({ s3_file_name: v || null })} />
              )}
            </div>
          </Step>

          <Step n={isAdvanced ? 3 : 4} title="Uji coba" description="Menjalankan query dengan batas baris — tanpa membuat file atau upload, dan semua perubahan data di-rollback.">
            <div className="flex flex-wrap items-end gap-3">
              {needsDate && (
                <Field label="Tanggal data" htmlFor="test_date">
                  <input id="test_date" type="date" className={`${inputClass} max-w-44`} value={testDate} onChange={(e) => setTestDate(e.target.value)} />
                </Field>
              )}
              <Field label="Batas baris" htmlFor="test_limit">
                <select id="test_limit" className={`${selectClass} max-w-28`} value={testLimit} onChange={(e) => setTestLimit(Number(e.target.value))}>
                  {[20, 100, 500].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </Field>
              <Button icon={FlaskConical} loading={test?.loading} disabled={!validation || validating || blockingForTest.length > 0 || dateDetectBlocks} onClick={runTest}>
                Jalankan uji coba
              </Button>
            </div>
            {blockingForTest.length > 0 && <p className="text-xs text-gray-500">Lengkapi isian di langkah sebelumnya untuk bisa uji coba.</p>}
            {test?.error && <Alert tone="error" title="Uji coba gagal">{test.error}</Alert>}
            {test?.result && (
              <TestResult
                result={test.result}
                stale={test.key !== currentKey}
                overrides={args.overrides}
                overrideTypes={meta.override_types}
                onOverride={setOverride}
                canOverride={tpl !== 'bronze_arcsde_view' || isAdmin}
                needsDate={needsDate}
              />
            )}
          </Step>

          <Step n={isAdvanced ? 4 : 5} title="Simpan">
            <Field
              label="Irama data"
              htmlFor="cadence"
              hint="Seberapa sering data sumber bertambah. Menentukan pembanding anomali volume: harian & snapshot per tanggal, mingguan & bulanan per periode lengkap."
            >
              <select id="cadence" className={`${selectClass} sm:w-72`} value={draft.cadence} onChange={(e) => setDraft((d) => ({ ...d, cadence: e.target.value }))}>
                <option value="">Otomatis{validation?.default_cadence ? ` (${CADENCE_LABEL[validation.default_cadence]})` : ''}</option>
                {Object.entries(CADENCE_LABEL).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            </Field>
            <Field label="Catatan (opsional)" htmlFor="notes" hint="Alasan pemilihan kolom, temuan data, dsb. — tampil di halaman detail job.">
              <textarea id="notes" rows={3} className={inputClass} value={draft.notes} onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} />
            </Field>
            {!isEdit && (
              <Toggle
                id="enable_now"
                label="Aktifkan sekarang"
                description={testedOk ? 'Job langsung bisa dijalankan (dan ikut jadwal CLI kalau namanya bronze_*).' : 'Butuh uji coba yang berhasil untuk isian saat ini.'}
                checked={enableNow && !!testedOk}
                onChange={(v) => testedOk && setEnableNow(v)}
              />
            )}
            {validation?.needs_s3_confirm && isAdmin && (
              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-200 bg-amber-50/60 px-4 py-3 text-sm text-amber-900">
                <input type="checkbox" checked={confirmMove} onChange={(e) => setConfirmMove(e.target.checked)} className="mt-0.5 h-4 w-4 accent-brand-600" />
                Saya paham path S3 job ini akan PINDAH. Data di path lama tidak ikut pindah dan tetap tertinggal di S3.
              </label>
            )}
            {saveError && <Alert tone="error">{saveError}</Alert>}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button variant="secondary" onClick={() => navigate(isEdit ? `/jobs/${enc(editName)}` : '/jobs')}>Batal</Button>
              <Button icon={Save} loading={saving} disabled={dateDetectBlocks || (validation?.needs_s3_confirm && !confirmMove)} onClick={save}>
                {isEdit ? 'Simpan perubahan' : 'Simpan job'}
              </Button>
            </div>
          </Step>
        </div>

        {/* Ringkasan live */}
        <div className="lg:sticky lg:top-8 lg:self-start">
          <Card className="space-y-4 p-4 sm:p-5">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-gray-800">Ringkasan</h2>
              {validating ? <Spinner /> : validation?.ok
                ? <span className="inline-flex items-center gap-1 text-xs font-medium text-brand-600"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Siap disimpan</span>
                : missingCount > 0 && !attempted && errorList.length === 0
                  ? <span className="text-xs text-gray-500">{missingCount} isian belum diisi</span>
                  : errorList.length > 0 && <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600"><CircleAlert className="h-3.5 w-3.5" aria-hidden /> {errorList.length} perlu diperbaiki</span>}
            </div>
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Template</dt>
                <dd className="mt-0.5 text-gray-800">{templateLabel(tpl)}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">Nama job</dt>
                <dd className="mt-0.5 break-all font-mono text-gray-800">{draft.name || '–'}</dd>
              </div>
              <div>
                <dt className="flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-gray-500">
                  Path S3 {preview?.example_date && `(${fmtDate(preview.example_date)})`}
                  {isEdit && existing?.s3_locked_at && <Lock className="h-3 w-3 text-amber-500" aria-label="terkunci" />}
                </dt>
                <dd className="mt-0.5 break-all font-mono text-xs text-gray-700">
                  {preview ? (preview.s3_key_example || 'tidak diupload ke S3') : '–'}
                </dd>
              </div>
            </dl>
            {errorList.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
                {errorList.slice(0, 8).map(([f, m]) => <li key={f}><span className="font-mono">{f === '_' ? '' : `${f}: `}</span>{m}</li>)}
              </ul>
            )}
            {validation?.warnings?.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {validation.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
            {preview?.query && (
              <div>
                <button type="button" onClick={() => setShowQuery((s) => !s)} aria-expanded={showQuery} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline">
                  Lihat query <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showQuery ? 'rotate-180' : ''}`} aria-hidden />
                </button>
                {showQuery && (
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-gray-900 px-3 py-2 font-mono text-[11px] leading-relaxed text-gray-300">
                    {preview.query.trim()}
                    {preview.params?.length > 0 && `\n\n-- params: ${JSON.stringify(preview.params)}`}
                  </pre>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
