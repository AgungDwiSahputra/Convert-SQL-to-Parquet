import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { BookOpen, ChevronRight, Plus, Search } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtNumber, fmtRelative } from '../lib/format'
import { Alert, Button, Card, EmptyState, Field, LoadingBlock, Modal, PageHeader, cx, inputClass } from '../components/ui'
import { KbNotes, PIPELINE_LABEL, SimpleKbBadge, changeText } from '../components/kb'

const selectClass = `${inputClass} pr-8`
const slug = (s) => (s || '').toLowerCase().replace(/^kbprep-/, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40)

function RegisterModal({ open, onClose, onCreated }) {
  const [kbId, setKbId] = useState('')
  const [lookup, setLookup] = useState(null) // {knowledge_base, data_sources}
  const [looking, setLooking] = useState(false)
  const [dsId, setDsId] = useState('')
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [pipeline, setPipeline] = useState('manual')
  const [dataset, setDataset] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const reset = () => {
    setKbId(''); setLookup(null); setDsId(''); setName(''); setTitle(''); setPipeline('manual'); setDataset(''); setNotes(''); setError(null)
  }
  const close = () => { reset(); onClose() }

  const doLookup = async () => {
    setLooking(true)
    setError(null)
    setLookup(null)
    setDsId('')
    try {
      const r = await api('/kb/lookup', { method: 'POST', body: { knowledge_base_id: kbId.trim() } })
      setLookup(r)
      if (r.data_sources.length === 1) pickSource(r.data_sources[0], r)
    } catch (e) {
      setError(e.message)
    } finally {
      setLooking(false)
    }
  }

  const pickSource = (ds, r = lookup) => {
    setDsId(ds.id)
    setName((n) => n || slug(ds.name))
    setTitle((t) => t || r?.knowledge_base?.name || ds.name || '')
  }

  const submit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const t = await api('/kb', {
        method: 'POST',
        body: { name, title, knowledge_base_id: kbId.trim(), data_source_id: dsId, pipeline, dataset: pipeline === 'documents' ? dataset : null, notes },
      })
      reset()
      onCreated(t)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Daftarkan Knowledge Base"
      description="Aplikasi hanya membaca status ingestion Knowledge Base ini; tidak memulai ingestion otomatis."
      footer={(
        <>
          <Button variant="secondary" onClick={close}>Batal</Button>
          <Button type="submit" form="kb-register" loading={saving} disabled={!dsId}>Daftarkan</Button>
        </>
      )}
    >
      <form id="kb-register" onSubmit={submit} className="space-y-5">
        <Field label="Knowledge Base ID" htmlFor="kb-id" hint="10 karakter huruf/angka, dari console Bedrock → Knowledge bases.">
          <div className="flex gap-2">
            <input id="kb-id" className={`${inputClass} font-mono`} value={kbId} onChange={(e) => setKbId(e.target.value)} placeholder="ABCDE12345" maxLength={10} autoComplete="off" />
            <Button variant="secondary" icon={Search} loading={looking} disabled={kbId.trim().length !== 10} onClick={doLookup}>Cek</Button>
          </div>
        </Field>

        {lookup && (
          <Field label={`Data source di “${lookup.knowledge_base.name}”`} htmlFor="kb-ds">
            {lookup.data_sources.length === 0 ? (
              <p className="text-sm text-gray-500">Knowledge Base ini belum punya data source.</p>
            ) : (
              <select id="kb-ds" className={selectClass} value={dsId} onChange={(e) => pickSource(lookup.data_sources.find((d) => d.id === e.target.value))}>
                <option value="">Pilih data source…</option>
                {lookup.data_sources.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.id}</option>)}
              </select>
            )}
          </Field>
        )}

        {dsId && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Nama target" htmlFor="kb-name" hint="Huruf kecil/angka/_/-. Tidak bisa diubah nanti.">
                <input id="kb-name" className={`${inputClass} font-mono`} required value={name} onChange={(e) => setName(e.target.value.toLowerCase())} />
              </Field>
              <Field label="Judul" htmlFor="kb-title">
                <input id="kb-title" className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
              </Field>
            </div>
            <Field label="Jalur sumber" htmlFor="kb-pipeline" hint="Jalur dokumen = Google Drive → rclone → Glue cleansing → KB. Pilih Manual untuk KB tanpa jalur itu (mis. few-shot).">
              <select id="kb-pipeline" className={selectClass} value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
                {Object.entries(PIPELINE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            {pipeline === 'documents' && (
              <Field label="Dataset jalur dokumen" htmlFor="kb-dataset" hint="Nama folder di documents/knowledge-base-preparation/v2/<dataset>/, mis. wm, hc_sop, agronomy_sop.">
                <input id="kb-dataset" className={`${inputClass} font-mono`} required value={dataset} onChange={(e) => setDataset(e.target.value)} />
              </Field>
            )}
            <Field label="Catatan" htmlFor="kb-notes">
              <textarea id="kb-notes" rows={2} className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} />
            </Field>
          </>
        )}
        {error && <Alert tone="error">{error}</Alert>}
      </form>
    </Modal>
  )
}

// SATU tampilan untuk semua peran. Bagian teknis (pesan sinkron gagal, kredensial AWS) hanya muncul bila server mengirim blok
// `tech` -- yaitu untuk Operator/Admin; tombol daftar khusus Admin.
export default function KnowledgeBasePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const isAdmin = hasRole(user, 'admin')
  const { data, error, loading } = useApi('/kb', { refreshMs: (d) => (d?.targets?.some((t) => t.status === 'updating') ? 15000 : 60000) })
  const [registering, setRegistering] = useState(false)
  const targets = data?.targets || []
  const mon = data?.monitor
  const tech = mon?.tech

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Kumpulan dokumen yang dipakai asisten AI, beserta kapan terakhir diperbarui."
        actions={isAdmin && <Button icon={Plus} onClick={() => setRegistering(true)}>Daftarkan Knowledge Base</Button>}
      />

      <div className="space-y-4">
        {error && <Alert tone="error">Status Knowledge Base belum bisa dimuat. Coba lagi beberapa saat lagi.</Alert>}
        {tech?.auth_mode === 'none' && (
          <Alert tone="warning" title="Kredensial AWS belum diatur">
            Isi <span className="font-mono">AWS_PROFILE</span> (IAM role) atau access key di file <span className="font-mono">.env</span> server, lalu restart web & worker.
          </Alert>
        )}
        {mon && !mon.enabled && <Alert tone="info">Status di halaman ini sedang tidak diperbarui otomatis.</Alert>}
        {tech?.last_result?.error && <Alert tone="error" title="Pemeriksaan terakhir gagal">{tech.last_result.error}</Alert>}

        {loading && !data ? (
          <Card><LoadingBlock /></Card>
        ) : targets.length === 0 ? (
          <Card>
            <EmptyState icon={BookOpen} title="Belum ada Knowledge Base" action={isAdmin && <Button icon={Plus} onClick={() => setRegistering(true)}>Daftarkan Knowledge Base</Button>}>
              {isAdmin ? 'Daftarkan Knowledge Base ID dan data source-nya untuk mulai memantau.' : 'Knowledge Base yang tersedia akan tampil di sini.'}
            </EmptyState>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {targets.map((t) => (
              <Link key={t.id} to={`/kb/${t.id}`} className={cx('group block rounded-xl border border-gray-200/80 bg-white p-4 shadow-card transition-colors hover:border-brand-200 sm:p-5', t.status === 'off' && 'opacity-70')}>
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 truncate font-semibold text-gray-800">{t.title}</p>
                  <SimpleKbBadge status={t.status} />
                </div>
                <div className="mt-2 space-y-1 text-sm text-gray-600">
                  <p className="flex flex-wrap gap-x-3 gap-y-0.5">
                    <span>{t.updated_at ? <>Diperbarui <span title={fmtDateTime(t.updated_at)}>{fmtRelative(t.updated_at)}</span></> : <span className="text-gray-400">Belum pernah diperbarui</span>}</span>
                    {t.documents != null && <span className="text-gray-500">{fmtNumber(t.documents)} dokumen</span>}
                  </p>
                  {t.last_change && <p className="text-xs text-gray-500">Perubahan terakhir: {changeText(t.last_change)}</p>}
                  <KbNotes t={t} />
                  {t.tech?.poll_error && <p className="text-xs text-amber-800">Sinkron riwayat gagal: {t.tech.poll_error}</p>}
                </div>
                <p className="mt-3 flex items-center gap-1 text-xs font-medium text-brand-700 opacity-80 group-hover:opacity-100">
                  Lihat rincian <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </p>
              </Link>
            ))}
          </div>
        )}
        {mon?.last_poll_at && <p className="text-xs text-gray-400">Status diperiksa otomatis · terakhir {fmtRelative(mon.last_poll_at)}.</p>}
      </div>

      <RegisterModal
        open={registering}
        onClose={() => setRegistering(false)}
        onCreated={(t) => { setRegistering(false); navigate(`/kb/${t.id}`) }}
      />
    </>
  )
}

