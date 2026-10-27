import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { CalendarClock, Pencil, Play, Plus, Search, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi, useDebounced } from '../lib/hooks'
import { fmtDateTime, fmtRelative, fmtUntil } from '../lib/format'
import { CADENCE_LABEL, NO_MODULE } from '../lib/jobs'
import {
  Alert, Badge, Button, Card, EmptyState, Field, IconChip, LoadingBlock, Modal, PageHeader, StatusBadge, Switch, Toggle, cx, inputClass,
} from '../components/ui'

const WEEKDAYS = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min']
const OFFSETS = [[0, 'Hari ini (H-0)'], [1, 'Kemarin (H-1)'], [2, '2 hari lalu (H-2)'], [3, '3 hari lalu (H-3)'], [7, '7 hari lalu (H-7)']]
const WINDOWS = [['offset', 'Satu tanggal'], ['last_days', 'Beberapa hari terakhir'], ['prev_week', 'Minggu lalu (Senin–Minggu)'], ['prev_month', 'Bulan lalu']]
const selectClass = `${inputClass} pr-8`

function selectorLabel(sel) {
  if (sel.mode === 'pattern') return `Pola nama: ${sel.patterns.join(', ')}`
  if (sel.mode === 'modules') return `Modul: ${sel.modules.join(', ')}`
  if (sel.mode === 'cadence') return `Irama: ${sel.cadences.map((c) => CADENCE_LABEL[c] || c).join(', ')}`
  return `${sel.jobs.length} job dipilih`
}

// Ringkasan rentang di form (versi server: scheduler.describe_window).
function windowSummary(f) {
  const end = f.date_offset === 0 ? 'hari ini' : `H-${f.date_offset}`
  if (f.window_mode === 'prev_week') return 'Senin–Minggu minggu sebelum tanggal jadwal'
  if (f.window_mode === 'prev_month') return 'tanggal 1 s/d akhir bulan sebelum tanggal jadwal'
  if (f.window_mode === 'last_days' && f.window_days > 1) return `H-${f.date_offset + f.window_days - 1} s/d ${end} (${f.window_days} hari)`
  return end
}

function Segmented({ value, onChange, options, label }) {
  return (
    <div className="grid gap-1 rounded-lg bg-gray-100 p-1" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }} role="radiogroup" aria-label={label}>
      {options.map(([k, text]) => (
        <button
          key={k}
          type="button"
          role="radio"
          aria-checked={value === k}
          onClick={() => onChange(k)}
          className={cx('h-9 rounded-md text-sm font-medium transition-colors', value === k ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-800')}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

const EMPTY = {
  name: '', enabled: false, frequency: 'daily', time_of_day: '06:00', weekdays: [], day_of_month: 1, date_offset: 1,
  window_mode: 'offset', window_days: 2,
  selector: { mode: 'pattern', patterns: ['bronze_*'], modules: [], jobs: [], cadences: [] },
  options: { skip_s3: false, skip_local: true, stop_on_error: false },
}

function ScheduleForm({ initial, onClose, onSaved }) {
  const isEdit = !!initial?.id
  const [form, setForm] = useState(() => (isEdit ? {
    ...EMPTY, ...initial, day_of_month: initial.day_of_month || 1, window_days: Math.max(initial.window_days || 1, 2),
    selector: { ...EMPTY.selector, ...initial.selector }, options: { ...EMPTY.options, ...initial.options },
  } : EMPTY))
  const [patternText, setPatternText] = useState(form.selector.patterns.join(', '))
  const [jobSearch, setJobSearch] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [preview, setPreview] = useState(null)
  const { data: jobsData } = useApi('/jobs')
  const jobs = useMemo(() => (jobsData?.jobs || []).filter((j) => j.enabled), [jobsData])
  const modules = useMemo(() => [...new Set(jobs.map((j) => j.s3?.module).filter(Boolean))].sort(), [jobs])

  const set = (patch) => setForm((f) => ({ ...f, ...patch }))
  const setSel = (patch) => setForm((f) => ({ ...f, selector: { ...f.selector, ...patch } }))
  const setOpt = (k, v) => setForm((f) => ({ ...f, options: { ...f.options, [k]: v } }))
  const toggleIn = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  useEffect(() => {
    setSel({ patterns: patternText.split(',').map((s) => s.trim()).filter(Boolean) })
  }, [patternText])

  const debouncedSel = useDebounced(form.selector, 350)
  useEffect(() => {
    let stale = false
    api('/schedules/preview', { method: 'POST', body: debouncedSel }).then((p) => !stale && setPreview(p)).catch(() => !stale && setPreview(null))
    return () => { stale = true }
  }, [debouncedSel])

  const visibleJobs = jobs.filter((j) => !jobSearch.trim() || j.name.includes(jobSearch.trim().toLowerCase()))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const body = {
        ...form,
        weekdays: form.frequency === 'weekly' ? form.weekdays : [],
        day_of_month: form.frequency === 'monthly' ? form.day_of_month : null,
        window_days: form.window_mode === 'last_days' ? form.window_days : 1,
      }
      for (const k of ['id', 'next_run_at', 'last_run_at', 'last_run_id', 'last_message', 'last_run_status', 'created_by', 'created_at', 'updated_by', 'updated_at', 'description', 'window_label', 'job_count', 'job_names', 'snapshot_count']) delete body[k]
      if (isEdit) await api(`/schedules/${initial.id}`, { method: 'PUT', body })
      else await api('/schedules', { method: 'POST', body })
      onSaved()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      size="lg"
      title={isEdit ? `Edit jadwal: ${initial.name}` : 'Tambah jadwal'}
      description="Saat jatuh tempo, worker membuat run otomatis — tercatat di Riwayat Run."
      footer={(
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>Batal</Button>
          <Button loading={saving} onClick={save}>{isEdit ? 'Simpan perubahan' : 'Simpan jadwal'}</Button>
        </>
      )}
    >
      <div className="space-y-5">
        {error && <Alert tone="error">{error}</Alert>}
        <Field label="Nama jadwal" htmlFor="s-name">
          <input id="s-name" className={inputClass} value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="mis. Bronze harian pagi" />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Frekuensi">
            <Segmented label="Frekuensi" value={form.frequency} onChange={(v) => set({ frequency: v })} options={[['daily', 'Harian'], ['weekly', 'Mingguan'], ['monthly', 'Bulanan']]} />
          </Field>
          <Field label="Jam (WIB)" htmlFor="s-time">
            <input id="s-time" type="time" className={inputClass} value={form.time_of_day} onChange={(e) => set({ time_of_day: e.target.value })} />
          </Field>
        </div>
        {form.frequency === 'weekly' && (
          <Field label="Hari">
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((d, i) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={form.weekdays.includes(i)}
                  onClick={() => set({ weekdays: toggleIn(form.weekdays, i).sort() })}
                  className={cx('h-9 w-12 rounded-lg border text-sm font-medium transition-colors',
                    form.weekdays.includes(i) ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50')}
                >
                  {d}
                </button>
              ))}
            </div>
          </Field>
        )}
        {form.frequency === 'monthly' && (
          <Field label="Tanggal" htmlFor="s-dom" hint="1–28 (tanggal 29–31 tidak ada di semua bulan).">
            <select id="s-dom" className={`${selectClass} sm:w-40`} value={form.day_of_month} onChange={(e) => set({ day_of_month: Number(e.target.value) })}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>Tanggal {d}</option>)}
            </select>
          </Field>
        )}
        <Field label="Rentang tanggal data" hint={`Dihitung dari tanggal jadwal, bukan jam run berjalan — run yang terlambat tetap memproses tanggal yang benar. Saat ini: ${windowSummary(form)}.`}>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
            <select aria-label="Jenis rentang" className={selectClass} value={form.window_mode} onChange={(e) => set({ window_mode: e.target.value })}>
              {WINDOWS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
            {form.window_mode === 'last_days' && (
              <div className="flex items-center gap-2">
                <input aria-label="Jumlah hari" type="number" min={2} max={31} className={`${inputClass} w-20`} value={form.window_days} onChange={(e) => set({ window_days: Math.min(31, Math.max(2, Number(e.target.value) || 2)) })} />
                <span className="text-sm text-gray-500">hari s/d</span>
              </div>
            )}
            {['offset', 'last_days'].includes(form.window_mode) && (
              <select aria-label="Tanggal (akhir)" className={selectClass} value={form.date_offset} onChange={(e) => set({ date_offset: Number(e.target.value) })}>
                {OFFSETS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            )}
          </div>
        </Field>

        <Field label="Cakupan job">
          <div className="space-y-3">
            <Segmented label="Cakupan" value={form.selector.mode} onChange={(v) => setSel({ mode: v })} options={[['pattern', 'Pola nama'], ['modules', 'Modul'], ['cadence', 'Irama'], ['jobs', 'Pilih job']]} />
            {form.selector.mode === 'cadence' && (
              <div className="space-y-1.5">
                <div className="flex flex-wrap gap-2">
                  {Object.entries(CADENCE_LABEL).map(([c, label]) => (
                    <button
                      key={c}
                      type="button"
                      aria-pressed={form.selector.cadences.includes(c)}
                      onClick={() => setSel({ cadences: toggleIn(form.selector.cadences, c) })}
                      className={cx('rounded-lg border px-3 py-1.5 text-sm transition-colors',
                        form.selector.cadences.includes(c) ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50')}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-500">Semua job aktif dengan irama terpilih (atur di form job). Job baru ikut otomatis.</p>
              </div>
            )}
            {form.selector.mode === 'pattern' && (
              <div className="space-y-1.5">
                <input aria-label="Pola nama job" className={`${inputClass} font-mono`} value={patternText} onChange={(e) => setPatternText(e.target.value)} placeholder="bronze_*, bronze_iot_*" />
                <p className="text-xs text-gray-500">Pisahkan dengan koma. <span className="font-mono">*</span> = apa saja. Job baru yang cocok otomatis ikut.</p>
              </div>
            )}
            {form.selector.mode === 'modules' && (
              <div className="flex flex-wrap gap-2">
                {modules.map((m) => (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={form.selector.modules.includes(m)}
                    onClick={() => setSel({ modules: toggleIn(form.selector.modules, m) })}
                    className={cx('rounded-lg border px-3 py-1.5 font-mono text-sm transition-colors',
                      form.selector.modules.includes(m) ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50')}
                  >
                    {m || NO_MODULE}
                  </button>
                ))}
              </div>
            )}
            {form.selector.mode === 'jobs' && (
              <div className="space-y-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
                  <input aria-label="Cari job" className={`${inputClass} pl-9`} placeholder="Cari job…" value={jobSearch} onChange={(e) => setJobSearch(e.target.value)} />
                </div>
                <ul className="max-h-52 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200">
                  {visibleJobs.map((j) => (
                    <li key={j.name}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-gray-50">
                        <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={form.selector.jobs.includes(j.name)} onChange={() => setSel({ jobs: toggleIn(form.selector.jobs, j.name) })} />
                        <span className="truncate text-gray-700">{j.name}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {preview?.snapshot_count > 0 && form.window_mode !== 'offset' && !(form.window_mode === 'last_days' && form.window_days < 2) && (
              <Alert tone="warning">
                {preview.snapshot_count} job snapshot (master) ikut cakupan. Dalam rentang lebih dari satu hari, master hanya diambil
                sekali dengan label tanggal <strong>awal</strong> rentang — buat jadwal master terpisah dengan satu tanggal.
              </Alert>
            )}
            {preview && (
              <div className="rounded-lg bg-brand-50 px-3 py-2.5 text-sm text-brand-900">
                <p className="font-semibold">{preview.count} job aktif cocok saat ini</p>
                {preview.jobs.length > 0 && (
                  <p className="mt-1 break-words font-mono text-xs text-brand-800/80">
                    {preview.jobs.slice(0, 8).join(', ')}{preview.count > 8 ? ` +${preview.count - 8} lainnya` : ''}
                  </p>
                )}
              </div>
            )}
          </div>
        </Field>

        <div className="space-y-4 rounded-xl border border-gray-200 p-4">
          <Toggle id="s-s3" label="Upload ke S3" description="Menimpa partisi S3 tanggal terkait." checked={!form.options.skip_s3} onChange={(v) => setOpt('skip_s3', !v)} />
          <Toggle id="s-local" label="Simpan file di server" description="Salinan .parquet di folder output/." checked={!form.options.skip_local} onChange={(v) => setOpt('skip_local', !v)} />
          <Toggle id="s-stop" label="Berhenti saat ada yang gagal" checked={form.options.stop_on_error} onChange={(v) => setOpt('stop_on_error', v)} />
        </div>

        <div className="space-y-2">
          <Toggle id="s-enabled" label="Aktifkan jadwal" description="Jadwal nonaktif tidak akan berjalan sendiri." checked={form.enabled} onChange={(v) => set({ enabled: v })} />
          {form.enabled && (
            <Alert tone="warning">
              Kalau Task Scheduler (<span className="font-mono">run_exporter.bat</span>) masih aktif untuk job yang sama, keduanya akan mengekspor dobel. Matikan salah satunya.
            </Alert>
          )}
        </div>
      </div>
    </Modal>
  )
}

export default function SchedulesPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const canEdit = hasRole(user, 'operator')
  const { data, error, loading, reload } = useApi('/schedules', { refreshMs: 30000 })
  const [editing, setEditing] = useState(null) // null | {} (baru) | jadwal
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState(null)
  const [message, setMessage] = useState(null)
  const schedules = data?.schedules || []

  const act = async (key, fn) => {
    setBusy(key)
    setMessage(null)
    try {
      await fn()
    } catch (e) {
      setMessage({ tone: 'error', text: e.message })
    } finally {
      setBusy(null)
    }
  }
  const toggle = (s) => act(`t${s.id}`, async () => {
    await api(`/schedules/${s.id}/enabled`, { method: 'POST', body: { enabled: !s.enabled } })
    await reload()
  })
  const runNow = (s) => act(`r${s.id}`, async () => {
    const r = await api(`/schedules/${s.id}/run-now`, { method: 'POST' })
    navigate(`/runs/${r.id}`)
  })
  const remove = () => act('del', async () => {
    await api(`/schedules/${deleting.id}`, { method: 'DELETE' })
    setDeleting(null)
    await reload()
  })

  if (loading && !data) return <LoadingBlock />

  return (
    <>
      <PageHeader
        title="Jadwal"
        description="Run otomatis pada jam tertentu (WIB). Dieksekusi worker dan tercatat di Riwayat Run."
        actions={canEdit && <Button icon={Plus} onClick={() => setEditing({})}>Tambah Jadwal</Button>}
      />
      {error && <Alert tone="error" className="mb-4">{error.message}</Alert>}
      {message && <Alert tone={message.tone} className="mb-4">{message.text}</Alert>}
      <Alert tone="info" className="mb-6">
        Selama jadwal lama di Task Scheduler (<span className="font-mono">run_exporter.bat</span>) masih aktif, jangan aktifkan jadwal yang mencakup job yang sama — ekspor akan berjalan dobel.
      </Alert>

      {schedules.length === 0 ? (
        <Card>
          <EmptyState icon={CalendarClock} title="Belum ada jadwal" action={canEdit && <Button icon={Plus} onClick={() => setEditing({})}>Tambah jadwal pertama</Button>}>
            Buat jadwal supaya ekspor berjalan otomatis setiap hari tanpa perlu dijalankan manual.
          </EmptyState>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {schedules.map((s) => (
            <Card key={s.id} className="flex flex-col p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="flex min-w-0 items-center gap-3">
                  <IconChip icon={CalendarClock} tone={s.enabled ? 'mint' : 'gray'} />
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-gray-800">{s.name}</p>
                    <p className="text-sm text-gray-500">{s.description}</p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={s.enabled ? 'mint' : 'gray'}>{s.enabled ? 'Aktif' : 'Nonaktif'}</Badge>
                  {canEdit && <Switch checked={s.enabled} disabled={busy === `t${s.id}`} onChange={() => toggle(s)} label={`${s.enabled ? 'Nonaktifkan' : 'Aktifkan'} ${s.name}`} />}
                </div>
              </div>

              <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-gray-500">Cakupan</dt>
                  <dd className="mt-0.5 break-words text-gray-800">{selectorLabel(s.selector)} <span className="text-gray-500">· {s.job_count} job</span></dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-500">Tanggal data</dt>
                  <dd className="mt-0.5 text-gray-800">{s.window_label}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-500">Berikutnya</dt>
                  <dd className="mt-0.5 text-gray-800">{s.next_run_at ? <>{fmtDateTime(s.next_run_at)} <span className="text-gray-500">· {fmtUntil(s.next_run_at)}</span></> : <span className="text-gray-500">Tidak dijadwalkan (nonaktif)</span>}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-500">Terakhir</dt>
                  <dd className="mt-0.5 flex items-center gap-2 text-gray-800">
                    {s.last_run_id ? (
                      <>
                        <Link to={`/runs/${s.last_run_id}`} className="font-medium text-brand-700 hover:underline">Run #{s.last_run_id}</Link>
                        {s.last_run_status && <StatusBadge status={s.last_run_status} />}
                      </>
                    ) : s.last_run_at ? fmtRelative(s.last_run_at) : 'Belum pernah'}
                  </dd>
                </div>
              </dl>
              {s.last_message && <p className="mt-3 text-xs text-gray-500">{s.last_message}</p>}

              {canEdit && (
                <div className="mt-5 flex flex-wrap gap-2 border-t border-gray-100 pt-4">
                  <Button size="sm" variant="soft" icon={Play} loading={busy === `r${s.id}`} onClick={() => runNow(s)}>Jalankan sekarang</Button>
                  <Button size="sm" variant="secondary" icon={Pencil} onClick={() => setEditing(s)}>Edit</Button>
                  <Button size="sm" variant="ghost" icon={Trash2} className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => setDeleting(s)}>Hapus</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {editing && <ScheduleForm initial={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
      <Modal
        open={!!deleting}
        onClose={() => busy !== 'del' && setDeleting(null)}
        size="md"
        title={`Hapus jadwal ${deleting?.name}?`}
        footer={(
          <>
            <Button variant="secondary" onClick={() => setDeleting(null)}>Batal</Button>
            <Button variant="danger" loading={busy === 'del'} onClick={remove}>Ya, hapus</Button>
          </>
        )}
      >
        <p className="text-sm text-gray-600">Run yang sudah pernah dibuat jadwal ini tetap ada di Riwayat Run.</p>
      </Modal>
    </>
  )
}
