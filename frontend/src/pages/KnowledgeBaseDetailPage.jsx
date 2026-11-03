import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtDuration, fmtNumber, fmtRelative } from '../lib/format'
import { Alert, Badge, Button, Card, CardHeader, Field, LoadingBlock, Modal, PageHeader, Switch, inputClass } from '../components/ui'
import { IngestionBadge, KbHealthBadge, PIPELINE_LABEL, StageBadge, originInfo } from '../components/kb'

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

function FlowCard({ flow, raw }) {
  const sched = flow.schedule
  const g = flow.glue
  const k = flow.kb
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Jalur dokumen" description="rclone → Glue cleansing → ingestion Knowledge Base · dibaca dari S3 (hanya daftar objek)" />
      {flow.error && <div className="px-5 pt-4"><Alert tone="warning" title="Sebagian status tidak dapat dibaca">{flow.error}</Alert></div>}
      <ul className="divide-y divide-gray-100">
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">1 · Salin dokumen (rclone)</p>
            <p className="mt-0.5 text-xs text-gray-500">
              {raw?.error ? raw.error : raw?.latest_at ? `File baru terakhir di raw/: ${fmtDateTime(raw.latest_at)} · ${fmtNumber(raw.objects)} objek${raw.partial ? '+' : ''}` : 'Belum dipindai.'}
            </p>
            <p className="mt-0.5 text-xs text-gray-400">Hanya informasi: rclone hanya mengunggah file yang berubah, jadi raw/ yang lama tidak berubah itu wajar.</p>
          </div>
          <Badge>Info</Badge>
        </li>
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">2 · Cleansing Glue</p>
            {g ? (
              <>
                <p className="mt-0.5 text-xs text-gray-500">Laporan run terakhir selesai {g.last_report_at ? fmtDateTime(g.last_report_at) : '–'}</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  Jadwal {sched.run_time} WIB · batas lapor {sched.deadline} WIB
                  {g.status === 'late' && <span className="text-red-600"> · belum ada laporan sesudah jadwal {fmtDateTime(g.expected_run_at)}</span>}
                </p>
              </>
            ) : <p className="mt-0.5 text-xs text-gray-400">Belum diperiksa.</p>}
          </div>
          {g && <StageBadge kind="glue" status={g.status} />}
        </li>
        <li className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-800">3 · Ingestion Knowledge Base</p>
            {k ? (
              <>
                <p className="mt-0.5 text-xs text-gray-500">{k.marker_at ? `Isi KB terakhir berubah ${fmtDateTime(k.marker_at)}` : 'Belum ada penanda perubahan.'}</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  Dianggap tertunda bila tidak ada ingestion sesudah perubahan selama {sched.pending_minutes} menit
                  {k.status === 'pending' && <span className="text-red-600"> · belum ada ingestion sejak perubahan itu</span>}
                </p>
              </>
            ) : <p className="mt-0.5 text-xs text-gray-400">Belum diperiksa.</p>}
          </div>
          {k && <StageBadge kind="kb" status={k.status} />}
        </li>
      </ul>
      {flow.checked_at && <p className="border-t border-gray-100 px-5 py-2.5 text-xs text-gray-400">Diperiksa {fmtRelative(flow.checked_at)}.</p>}
    </Card>
  )
}

export default function KnowledgeBaseDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = hasRole(user, 'admin')
  const isOperator = hasRole(user, 'operator')
  const { data, error, loading, reload } = useApi(`/kb/${id}`, { refreshMs: (d) => (d?.target?.running ? 15000 : 60000) })
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState(null)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  if (loading && !data) return <LoadingBlock />
  if (error && !data) {
    return (
      <>
        <Link to="/kb" className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline"><ArrowLeft className="h-4 w-4" aria-hidden />Knowledge Base</Link>
        <Alert tone="error">{error.message}</Alert>
      </>
    )
  }
  const { target: t, ingestions } = data

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
      <Link to="/kb" className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
        <ArrowLeft className="h-4 w-4" aria-hidden />Knowledge Base
      </Link>
      <PageHeader
        title={t.title || t.name}
        description={t.notes || undefined}
        actions={(
          <>
            {isOperator && <Button variant="secondary" icon={RefreshCw} loading={refreshing} onClick={refresh}>Muat ulang riwayat</Button>}
            {isAdmin && <Button variant="secondary" icon={Pencil} onClick={() => setEditing(true)}>Pengaturan</Button>}
            {isAdmin && <Button variant="ghost" icon={Trash2} onClick={() => setConfirmDelete(true)}>Hapus</Button>}
          </>
        )}
      />

      <div className="space-y-4">
        {message && <Alert tone={message.tone}>{message.text}</Alert>}
        {t.poll_error && <Alert tone="warning" title="Sinkron terakhir gagal">{t.poll_error}</Alert>}
        {!t.enabled && <Alert tone="info">Target ini nonaktif — worker tidak menyinkronkan riwayatnya.</Alert>}

        <Card className="p-5">
          <div className="flex flex-wrap items-center gap-3">
            <KbHealthBadge health={t.health} />
            {t.pipeline === 'documents' && <Badge tone="blue">Jalur dokumen{t.dataset ? ` · ${t.dataset}` : ''}</Badge>}
            <span className="text-xs text-gray-500" title={t.polled_at ? fmtDateTime(t.polled_at) : undefined}>
              {t.polled_at ? `Riwayat disinkronkan ${fmtRelative(t.polled_at)}` : 'Belum pernah disinkronkan'}
            </span>
          </div>
          <dl className="mt-4 grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-gray-500">Knowledge Base ID</dt><dd className="mt-0.5 font-mono text-gray-800">{t.knowledge_base_id}</dd></div>
            <div><dt className="text-xs text-gray-500">Data Source ID</dt><dd className="mt-0.5 font-mono text-gray-800">{t.data_source_id}</dd></div>
            <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Sumber di S3 (prefix)</dt><dd className="mt-0.5 break-all font-mono text-gray-800">{t.source_prefix || '(seluruh bucket)'}</dd></div>
          </dl>
        </Card>

        {t.flow && <FlowCard flow={t.flow} raw={data.monitor?.raw} />}

        <Card className="overflow-hidden">
          <CardHeader title="Riwayat ingestion" description="50 terbaru, termasuk yang dimulai job Glue atau console AWS" />
          {ingestions.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-gray-500">Belum ada ingestion tercatat untuk data source ini.</p>
          ) : (
            <div className="overflow-x-auto">
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
                      <td className="px-4 py-3 text-gray-800" title={i.ingestion_job_id}>
                        {fmtDateTime(i.started_at)}
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
          )}
        </Card>
      </div>

      <EditModal open={editing} target={t} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload() }} />
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        size="md"
        title="Hapus target?"
        description={`“${t.title || t.name}” dan riwayat ingestion yang tersimpan di aplikasi akan dihapus. Knowledge Base di AWS tidak terpengaruh.`}
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
