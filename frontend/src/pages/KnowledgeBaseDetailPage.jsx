import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, History, Pencil, Play, RefreshCw, Trash2, Workflow } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtDuration, fmtNumber, fmtRelative } from '../lib/format'
import { Alert, Badge, Button, Card, CardHeader, Field, LoadingBlock, Modal, PageHeader, Switch, TechDetails, inputClass } from '../components/ui'
import {
  ActionStatusBadge, IngestionBadge, KbNotes, PIPELINE_LABEL, SimpleKbBadge, SimpleResultBadge, StageBadge, StepList, changeText, originInfo,
} from '../components/kb'

const selectClass = `${inputClass} pr-8`

function EditModal({ open, target, onClose, onSaved }) {
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [pipeline, setPipeline] = useState('manual')
  const [dataset, setDataset] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (open) {
      setTitle(target.title || '')
      setNotes(target.notes || '')
      setPipeline(target.pipeline)
      setDataset(target.dataset || '')
      setEnabled(target.enabled)
      setError(null)
    }
  }, [open, target])

  const submit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api(`/kb/${target.id}`, { method: 'PUT', body: { title, notes, pipeline, dataset: pipeline === 'documents' ? dataset : null, enabled } })
      onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Pengaturan target"
      description="Knowledge Base ID, data source, dan nama target tidak bisa diubah — daftarkan ulang bila salah."
      footer={(
        <>
          <Button variant="secondary" onClick={onClose}>Batal</Button>
          <Button type="submit" form="kb-edit" loading={saving}>Simpan</Button>
        </>
      )}
    >
      <form id="kb-edit" onSubmit={submit} className="space-y-5">
        <Field label="Judul" htmlFor="e-title">
          <input id="e-title" className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
        </Field>
        <Field label="Jalur sumber" htmlFor="e-pipeline">
          <select id="e-pipeline" className={selectClass} value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
            {Object.entries(PIPELINE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        {pipeline === 'documents' && (
          <Field label="Dataset jalur dokumen" htmlFor="e-dataset" hint="Nama folder di documents/knowledge-base-preparation/v2/<dataset>/.">
            <input id="e-dataset" className={`${inputClass} font-mono`} required value={dataset} onChange={(e) => setDataset(e.target.value)} />
          </Field>
        )}
        <Field label="Catatan" htmlFor="e-notes">
          <textarea id="e-notes" rows={3} className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} />
        </Field>
        <div className="flex items-center justify-between gap-4 rounded-xl border border-gray-200/80 p-4">
          <div>
            <p className="text-sm font-medium text-gray-800">Dipantau</p>
            <p className="text-xs text-gray-500">Nonaktif = worker berhenti menyinkronkan riwayat dan mengirim email untuk target ini.</p>
          </div>
          <Switch checked={enabled} onChange={setEnabled} label="Dipantau" />
        </div>
        {error && <Alert tone="error">{error}</Alert>}
      </form>
    </Modal>
  )
}

/** Rincian teknis tiga tahap jalur dokumen (rclone → Glue → ingestion), di dalam "Detail teknis". */
function FlowDetails({ flow, raw }) {
  const sched = flow.schedule
  const g = flow.glue
  const k = flow.kb
  return (
    <div className="space-y-3">
      {flow.error && <Alert tone="warning" title="Sebagian status tidak dapat dibaca">{flow.error}</Alert>}
      {flow.stale && flow.checked_at && (
        <Alert tone="warning" title="Status belum diperbarui">Terakhir diperiksa {fmtRelative(flow.checked_at)}. Pastikan worker berjalan; status “berproses” tidak ditampilkan dari data yang sudah basi.</Alert>
      )}
      <ul className="divide-y divide-gray-100 rounded-lg border border-gray-100">
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">1 · Salin dokumen dari Google Drive (rclone)</p>
            <p className="mt-0.5 text-xs text-gray-500">
              {raw?.error ? raw.error : raw?.latest_at ? `File baru terakhir di raw/: ${fmtDateTime(raw.latest_at)} · ${fmtNumber(raw.objects)} objek${raw.partial ? '+' : ''}` : 'Belum dipindai.'}
            </p>
            <p className="mt-0.5 text-xs text-gray-400">Hanya informasi: rclone hanya mengunggah file yang berubah, jadi raw/ yang lama tidak berubah itu wajar.</p>
          </div>
          <Badge>Info</Badge>
        </li>
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">2 · Pembersihan dokumen (Glue cleansing)</p>
            {g ? (
              <>
                {g.status === 'running' && g.run && (
                  <p className="mt-0.5 text-xs font-medium text-blue-700">
                    Run Glue sedang berjalan sejak {fmtDateTime(g.run.started_at)} ({fmtDuration((Date.now() - new Date(g.run.started_at).getTime()) / 1000)}) — ingestion dimulai Glue di akhir run.
                  </p>
                )}
                {g.status === 'failed' && g.run && (
                  <p className="mt-0.5 text-xs font-medium text-red-600">Run Glue terakhir {g.run.state} (mulai {fmtDateTime(g.run.started_at)}) — periksa job di Glue console.</p>
                )}
                <p className="mt-0.5 text-xs text-gray-500">Laporan run terakhir selesai {g.last_report_at ? fmtDateTime(g.last_report_at) : '–'}</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  Jadwal {sched.run_time} WIB · batas lapor {sched.deadline} WIB
                  {g.report_status === 'late' && <span className="text-red-600"> · belum ada laporan sesudah jadwal {fmtDateTime(g.expected_run_at)}</span>}
                </p>
                {flow.glue_error && <p className="mt-0.5 text-xs text-amber-700">Status run Glue tidak terbaca ({flow.glue_error}) — status dinilai dari laporan saja.</p>}
              </>
            ) : <p className="mt-0.5 text-xs text-gray-400">Belum diperiksa.</p>}
          </div>
          {g && <StageBadge kind="glue" status={g.status} />}
        </li>
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">3 · Pembaruan Knowledge Base (ingestion)</p>
            {k ? (
              <>
                <p className="mt-0.5 text-xs text-gray-500">{k.marker_at ? `Isi KB terakhir berubah ${fmtDateTime(k.marker_at)}` : 'Belum ada penanda perubahan.'}</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  Dianggap tertunda bila tidak ada ingestion sesudah perubahan selama {sched.pending_minutes} menit
                  {k.status === 'pending' && <span className="text-red-600"> · belum ada ingestion sejak perubahan itu</span>}
                </p>
                {k.status === 'waiting' && g?.status === 'running' && (
                  <p className="mt-0.5 text-xs font-medium text-blue-700">Menunggu run Glue selesai — Glue memulai ingestion di akhir run.</p>
                )}
              </>
            ) : <p className="mt-0.5 text-xs text-gray-400">Belum diperiksa.</p>}
          </div>
          {k && <StageBadge kind="kb" status={k.status} />}
        </li>
      </ul>
      <p className="text-xs text-gray-400">
        Dibaca dari daftar objek S3 + status run Glue{flow.checked_at ? ` · diperiksa ${fmtRelative(flow.checked_at)}` : ''}{sched.glue_job ? <> · job <span className="font-mono">{sched.glue_job}</span></> : null}.
      </p>
    </div>
  )
}

/** Tabel teknis riwayat ingestion (50 terbaru) di dalam "Detail teknis". */
function IngestionTable({ ingestions }) {
  if (ingestions.length === 0) return <p className="text-sm text-gray-500">Belum ada ingestion tercatat untuk data source ini.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-100">
      <table className="w-full min-w-[44rem] text-left text-sm">
        <thead className="bg-gray-50 text-xs font-medium text-gray-500">
          <tr>
            <th className="px-4 py-2.5">Mulai (WIB)</th>
            <th className="px-3 py-2.5">Status</th>
            <th className="px-3 py-2.5 text-right">Durasi</th>
            <th className="px-3 py-2.5 text-right">Dipindai</th>
            <th className="px-3 py-2.5 text-right">Baru</th>
            <th className="px-3 py-2.5 text-right">Diubah</th>
            <th className="px-3 py-2.5 text-right">Dihapus</th>
            <th className="px-3 py-2.5 text-right">Gagal</th>
            <th className="px-4 py-2.5">Asal</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {ingestions.map((i) => (
            <tr key={i.id} className="align-top">
              <td className="px-4 py-3 text-gray-800">
                {fmtDateTime(i.started_at)}
                <span className="block font-mono text-[11px] text-gray-400">{i.ingestion_job_id}</span>
                {i.failure_reasons.length > 0 && (
                  <ul className="mt-1.5 max-w-xs list-disc space-y-0.5 pl-4 text-xs text-amber-700">
                    {i.failure_reasons.slice(0, 3).map((r, k) => <li key={k} className="break-words">{r}</li>)}
                    {i.failure_reasons.length > 3 && <li>+{i.failure_reasons.length - 3} lainnya</li>}
                  </ul>
                )}
              </td>
              <td className="px-3 py-3"><IngestionBadge status={i.status} /></td>
              <td className="px-3 py-3 text-right tabular-nums text-gray-600">{fmtDuration(i.duration_s)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-gray-600">{fmtNumber(i.scanned)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-gray-600">{fmtNumber(i.new_indexed)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-gray-600">{fmtNumber(i.modified_indexed)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-gray-600">{fmtNumber(i.deleted)}</td>
              <td className={`px-3 py-3 text-right tabular-nums ${i.failed ? 'font-semibold text-red-700' : 'text-gray-600'}`}>{fmtNumber(i.failed)}</td>
              <td className="px-4 py-3 text-xs text-gray-500" title={originInfo(i).hint}>{originInfo(i).label}{i.started_by ? ` · ${i.started_by}` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const ACTION_INFO = {
  glue_run: {
    title: 'Pembersihan dokumen',
    desc: 'Menjalankan pembersihan dokumen (job Glue) sekarang. Di akhir proses, Knowledge Base diperbarui otomatis bila ada dokumen berubah.',
    button: 'Jalankan pembersihan',
    icon: Play,
    confirm: (t, job) => `Job ${job} akan dijalankan sekarang. Run tanpa dokumen berubah selesai dalam hitungan menit; dokumen baru diproses model vision (berbiaya) dan run WM bisa berjalan berjam-jam. Lanjutkan?`,
  },
  ingestion: {
    title: 'Pembaruan Knowledge Base',
    desc: 'Memasukkan dokumen ke Knowledge Base sekarang (ingestion Bedrock, tanpa pembersihan). Untuk saat pembaruan otomatis tidak berjalan, atau Knowledge Base tanpa jalur pembersihan.',
    button: 'Perbarui sekarang',
    icon: RefreshCw,
    confirm: (t) => (t.pipeline === 'documents'
      ? 'Bedrock akan mengindeks perubahan di data source ini (inkremental). Glue tidak mengetahui sync ini, jadi di akhir run berikutnya Glue mungkin memulai satu ingestion lagi. Lanjutkan?'
      : 'Bedrock akan mengindeks perubahan di data source ini (inkremental). Lanjutkan?'),
  },
}

function ManualActionsCard({ target, flow, availability, actions, canRun, onRequested }) {
  const [pending, setPending] = useState(null) // aksi yang sedang dikonfirmasi
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const order = ['glue_run', 'ingestion'].filter((a) => availability?.[a])
  const job = flow?.schedule?.glue_job

  const send = async () => {
    setBusy(true)
    setError(null)
    try {
      await api(`/kb/${target.id}/actions`, { method: 'POST', body: { action: pending } })
      setPending(null)
      onRequested()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader title="Proses manual" description="Dijalankan oleh proses otomatis dalam ±20 detik; syarat diperiksa ulang dengan data terbaru sebelum dimulai" />
      <ul className="divide-y divide-gray-100">
        {order.map((a) => {
          const info = ACTION_INFO[a]
          const av = availability[a]
          return (
            <li key={a} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-800">{info.title}</p>
                <p className="mt-0.5 text-xs text-gray-500">{info.desc}</p>
                {!av.enabled && av.reason && <p className="mt-1 text-xs text-amber-700">{av.reason}</p>}
              </div>
              {canRun && (
                <Button size="sm" variant="secondary" icon={info.icon} disabled={!av.enabled} onClick={() => { setError(null); setPending(a) }}>
                  {info.button}
                </Button>
              )}
            </li>
          )
        })}
      </ul>
      {!canRun && <p className="border-t border-gray-100 px-5 py-2.5 text-xs text-gray-400">Hanya Operator dan Admin yang bisa menjalankan proses manual.</p>}
      {actions.length > 0 && (
        <div className="border-t border-gray-100 px-5 py-3">
          <p className="mb-2 text-xs font-medium text-gray-500">Riwayat proses manual</p>
          <ul className="space-y-2">
            {actions.map((x) => (
              <li key={x.id} className="flex items-start justify-between gap-3 text-xs">
                <div className="min-w-0">
                  <p className="text-gray-700">
                    <span className="font-medium">{x.label}</span> · {x.requested_by} · <span title={fmtDateTime(x.requested_at)}>{fmtRelative(x.requested_at)}</span>
                  </p>
                  {x.message && <p className="break-words text-gray-500">{x.message}</p>}
                </div>
                <ActionStatusBadge status={x.status} />
              </li>
            ))}
          </ul>
        </div>
      )}
      <Modal
        open={!!pending}
        onClose={() => setPending(null)}
        size="md"
        title={pending ? ACTION_INFO[pending].title : ''}
        description={target.title || target.name}
        footer={(
          <>
            <Button variant="secondary" onClick={() => setPending(null)}>Batal</Button>
            <Button loading={busy} onClick={send}>{pending ? ACTION_INFO[pending].button : ''}</Button>
          </>
        )}
      >
        {pending && <p className="text-sm text-gray-600">{ACTION_INFO[pending].confirm(target, job)}</p>}
        <p className="mt-3 text-xs text-gray-500">Worker memulai dalam ±20 detik setelah memeriksa ulang syarat; hasilnya tampil di riwayat di bawah.</p>
        {error && <div className="mt-3"><Alert tone="error">{error}</Alert></div>}
      </Modal>
    </Card>
  )
}

// SATU tampilan untuk semua peran. Bagian sederhana dari `target`/`ingestions` (semua peran); "Detail teknis", proses manual, dan
// tombol kelola muncul bila server mengirim `tech`/`availability` -- yaitu untuk Operator/Admin.
export default function KnowledgeBaseDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = hasRole(user, 'admin')
  const isOperator = hasRole(user, 'operator')
  const { data, error, loading, reload } = useApi(`/kb/${id}`, {
    refreshMs: (d) => (d?.actions?.some((a) => a.status === 'queued' || a.status === 'running') ? 5000 : d?.target?.status === 'updating' ? 15000 : 60000),
  })
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState(null)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const back = (
    <Link to="/kb" className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
      <ArrowLeft className="h-4 w-4" aria-hidden />Knowledge Base
    </Link>
  )
  if (loading && !data) return <LoadingBlock />
  if (error && !data) {
    return <>{back}<Alert tone="error">{error.status === 404 ? 'Knowledge Base ini tidak tersedia.' : error.message}</Alert></>
  }
  const t = data.target
  const tech = data.tech // hanya Operator/Admin
  const full = tech?.target

  const refresh = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      await api(`/kb/${t.id}/refresh`, { method: 'POST' })
      await reload()
    } catch (e) {
      setMessage({ tone: 'error', text: e.message })
    } finally {
      setRefreshing(false)
    }
  }

  const remove = async () => {
    setDeleting(true)
    try {
      await api(`/kb/${t.id}`, { method: 'DELETE' })
      navigate('/kb')
    } catch (e) {
      setConfirmDelete(false)
      setMessage({ tone: 'error', text: e.message })
      setDeleting(false)
    }
  }

  return (
    <>
      {back}
      <PageHeader
        title={<span className="inline-flex flex-wrap items-center gap-3">{t.title}<SimpleKbBadge status={t.status} /></span>}
        description={full?.notes || undefined}
        actions={(
          <>
            {isOperator && tech && <Button variant="secondary" icon={RefreshCw} loading={refreshing} onClick={refresh}>Muat ulang riwayat</Button>}
            {isAdmin && full && <Button variant="secondary" icon={Pencil} onClick={() => setEditing(true)}>Pengaturan</Button>}
            {isAdmin && full && <Button variant="ghost" icon={Trash2} onClick={() => setConfirmDelete(true)}>Hapus</Button>}
          </>
        )}
      />

      <div className="space-y-4">
        {message && <Alert tone={message.tone}>{message.text}</Alert>}
        {full?.poll_error && <Alert tone="warning" title="Sinkron riwayat terakhir gagal">{full.poll_error}</Alert>}
        {t.status === 'off' && <Alert tone="info">Knowledge Base ini tidak dipantau — riwayatnya tidak diperbarui otomatis.</Alert>}

        <Card className="overflow-hidden">
          <div className="grid divide-y divide-gray-100 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            <div className="px-5 py-4">
              <p className="text-xs text-gray-500">Terakhir diperbarui</p>
              <p className="mt-1 font-semibold text-gray-800">{t.updated_at ? fmtRelative(t.updated_at) : '–'}</p>
              {t.updated_at && <p className="text-xs text-gray-500">{fmtDateTime(t.updated_at)}</p>}
            </div>
            <div className="px-5 py-4">
              <p className="text-xs text-gray-500">Jumlah dokumen</p>
              <p className="mt-1 font-semibold text-gray-800">{t.documents != null ? fmtNumber(t.documents) : '–'}</p>
            </div>
            <div className="px-5 py-4">
              <p className="text-xs text-gray-500">Perubahan terakhir</p>
              <p className="mt-1 font-semibold text-gray-800">{changeText(t.last_change) || '–'}</p>
            </div>
          </div>
          {(t.reason || t.last_attempt_failed || t.failed_documents > 0) && <div className="space-y-1 border-t border-gray-100 px-5 py-3"><KbNotes t={t} /></div>}
          {full && (
            <TechDetails storageKey="kb-ids" summary="ID Knowledge Base & data source, prefix S3">
              <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                <div><dt className="text-xs text-gray-500">Knowledge Base ID</dt><dd className="mt-0.5 font-mono text-gray-800">{full.knowledge_base_id}</dd></div>
                <div><dt className="text-xs text-gray-500">Data Source ID</dt><dd className="mt-0.5 font-mono text-gray-800">{full.data_source_id}</dd></div>
                <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Sumber di S3 (prefix)</dt><dd className="mt-0.5 break-all font-mono text-gray-800">{full.source_prefix || '(seluruh bucket)'}</dd></div>
                <div><dt className="text-xs text-gray-500">Nama target · jalur</dt><dd className="mt-0.5 text-gray-800"><span className="font-mono">{full.name}</span> · {PIPELINE_LABEL[full.pipeline]}{full.dataset ? ` · ${full.dataset}` : ''}</dd></div>
                <div><dt className="text-xs text-gray-500">Riwayat disinkronkan</dt><dd className="mt-0.5 text-gray-800">{full.polled_at ? fmtDateTime(full.polled_at) : 'Belum pernah'}</dd></div>
              </dl>
            </TechDetails>
          )}
        </Card>

        {t.steps && (
          <Card className="overflow-hidden">
            <CardHeader icon={Workflow} title="Alur pembaruan dokumen" description="Dokumen dari Google Drive disalin, dibersihkan, lalu dimasukkan ke Knowledge Base — otomatis setiap malam" />
            <div className="p-4 sm:p-5"><StepList steps={t.steps} /></div>
            {full?.flow && (
              <TechDetails storageKey="kb-flow" summary="rclone, run Glue, penanda perubahan, jadwal & batas">
                <FlowDetails flow={full.flow} raw={tech.monitor?.raw} />
              </TechDetails>
            )}
          </Card>
        )}

        {data.availability && full && (
          <ManualActionsCard target={full} flow={full.flow} availability={data.availability} actions={data.actions || []} canRun={isOperator} onRequested={reload} />
        )}

        <Card className="overflow-hidden">
          <CardHeader icon={History} title="Riwayat pembaruan" description="10 pembaruan terakhir" />
          {data.ingestions.length === 0 ? (
            <p className="px-5 py-4 text-sm text-gray-500">Belum ada pembaruan tercatat.</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {data.ingestions.map((i) => (
                <li key={i.started_at} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3">
                  <span className="w-40 shrink-0 text-sm text-gray-700">{fmtDateTime(i.started_at)}</span>
                  <SimpleResultBadge status={i.status} />
                  <span className="min-w-0 flex-1 text-sm text-gray-600">
                    {i.status === 'done' ? changeText(i) : i.status === 'running' ? 'Sedang diproses…' : 'Pembaruan tidak selesai'}
                    {i.failed_documents > 0 && <span className="text-amber-800"> · {fmtNumber(i.failed_documents)} dokumen belum bisa diproses</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {tech && (
            <TechDetails storageKey="kb-hist" summary="50 ingestion terbaru: ID job, durasi, dipindai, gagal, asal, alasan gagal">
              <IngestionTable ingestions={tech.ingestions} />
            </TechDetails>
          )}
        </Card>
      </div>

      {full && <EditModal open={editing} target={full} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload() }} />}
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        size="md"
        title="Hapus target?"
        description={`“${t.title}” dan riwayat pembaruan yang tersimpan di aplikasi akan dihapus. Knowledge Base di AWS tidak terpengaruh.`}
        footer={(
          <>
            <Button variant="secondary" onClick={() => setConfirmDelete(false)}>Batal</Button>
            <Button variant="danger" loading={deleting} onClick={remove}>Hapus target</Button>
          </>
        )}
      >
        <p className="text-sm text-gray-600">Anda bisa mendaftarkannya lagi kapan saja; riwayat akan disinkronkan ulang dari Bedrock.</p>
      </Modal>
    </>
  )
}

