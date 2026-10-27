import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, ChevronDown, History, Lock, LockOpen, Pencil, Play, Power, PowerOff } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDate, fmtDateTime, fmtNumber, fmtRelative, reflow } from '../lib/format'
import { CADENCE_LABEL, HISTORY_LABEL, STRDATE_LABEL, TYPE_LABEL, diffSnapshots, jobType } from '../lib/jobs'
import { Alert, Badge, Button, Card, DqBadge, LoadingBlock, Modal, StatusBadge } from '../components/ui'
import { EnabledBadge } from './JobsPage'

function Section({ title, icon: Icon, children, action }) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-800">
          {Icon && <Icon className="h-4 w-4 text-gray-400" aria-hidden />} {title}
        </h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </Card>
  )
}

function Row({ label, children }) {
  return (
    <div className="grid gap-1 py-2.5 sm:grid-cols-[11rem_1fr] sm:gap-4">
      <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-gray-800">{children}</dd>
    </div>
  )
}

function HistoryItem({ h }) {
  const changes = diffSnapshots(h.before, h.after)
  return (
    <li className="relative pl-6">
      <span className="absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full bg-brand-500 ring-4 ring-brand-100" aria-hidden />
      <p className="text-sm font-medium text-gray-800">{HISTORY_LABEL[h.action] || h.action}</p>
      <p className="text-xs text-gray-500" title={fmtDateTime(h.changed_at)}>
        {h.changed_by} · {fmtRelative(h.changed_at)}
      </p>
      {changes.length > 0 && h.action !== 'enable' && h.action !== 'disable' && (
        <ul className="mt-1.5 space-y-0.5">
          {changes.slice(0, 8).map((c) => (
            <li key={c.field} className="break-all font-mono text-[11px] text-gray-600">
              <span className="text-gray-500">{c.field}:</span> <span className="text-red-600/80 line-through">{c.from.slice(0, 80)}</span>{' '}
              → <span className="text-brand-700">{c.to.slice(0, 80)}</span>
            </li>
          ))}
          {changes.length > 8 && <li className="text-[11px] text-gray-500">+{changes.length - 8} perubahan lain</li>}
        </ul>
      )}
    </li>
  )
}

export default function JobDetailPage() {
  const { name } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { data: job, error, loading, reload } = useApi(`/jobs/${encodeURIComponent(name)}`)
  const [showQuery, setShowQuery] = useState(false)
  const [confirmToggle, setConfirmToggle] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  if (loading && !job) return <LoadingBlock />
  if (error && !job) {
    return (
      <div className="space-y-4">
        <Alert tone="error">{error.message}</Alert>
        <Link to="/jobs" className="text-sm font-medium text-brand-600 hover:underline">← Kembali ke daftar job</Link>
      </div>
    )
  }

  const t = jobType(job)
  const toggle = async () => {
    setBusy(true)
    setActionError(null)
    try {
      await api(`/jobs/${encodeURIComponent(job.name)}/enabled`, { method: 'POST', body: { enabled: !job.enabled } })
      await reload()
      setConfirmToggle(false)
    } catch (e) {
      setActionError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Daftar Job
        </Link>
        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="break-all text-2xl font-semibold tracking-tight text-gray-800">{job.name}</h1>
              <EnabledBadge enabled={job.enabled} />
              <Badge tone="blue">{TYPE_LABEL[t]}</Badge>
            </div>
            <p className="mt-1 text-sm text-gray-500">{job.template_label}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {job.can_edit && (
              <Button variant="secondary" icon={job.enabled ? PowerOff : Power} onClick={() => setConfirmToggle(true)}>
                {job.enabled ? 'Nonaktifkan' : 'Aktifkan'}
              </Button>
            )}
            {job.can_edit && (
              <Button variant="secondary" icon={Pencil} onClick={() => navigate(`/jobs/${encodeURIComponent(job.name)}/edit`)}>Edit</Button>
            )}
            {hasRole(user, 'operator') && job.enabled && (
              <Button icon={Play} onClick={() => navigate(`/runs/new?jobs=${encodeURIComponent(job.name)}`)}>Jalankan</Button>
            )}
          </div>
        </div>
      </div>

      {!job.can_edit && hasRole(user, 'operator') && (
        <Alert tone="info">Job ini memakai template khusus admin — hanya admin yang bisa mengubah atau mengaktifkannya.</Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Section title="Ringkasan">
            <dl className="divide-y divide-gray-100">
              {job.args?.table && <Row label="Sumber"><span className="font-mono">{job.args.db}.dbo.{job.args.table}</span></Row>}
              {job.args?.date_col && <Row label="Kolom tanggal"><span className="font-mono">{job.args.date_col}</span>{job.args.date_format ? ` (format ${STRDATE_LABEL[job.args.date_format] || job.args.date_format})` : ''}</Row>}
              <Row label="File output"><span className="font-mono">{job.output}</span></Row>
              <Row label="Folder S3">{job.s3_folder ? <span className="font-mono">{job.s3_folder}</span> : 'Tidak diupload ke S3'}</Row>
              {job.s3_folder && (
                <Row label="Contoh path S3">
                  <span className="break-all font-mono text-xs">{job.s3_key_example || 'ditentukan dari data (partisi mingguan)'}</span>
                </Row>
              )}
              {job.s3_folder && (
                <Row label="Kunci path S3">
                  {job.s3_locked_at ? (
                    <span className="inline-flex items-center gap-1.5 text-amber-700">
                      <Lock className="h-3.5 w-3.5" aria-hidden /> Terkunci sejak {fmtDate(job.s3_locked_at.slice(0, 10))} — hanya admin yang bisa memindahkan
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-gray-600">
                      <LockOpen className="h-3.5 w-3.5" aria-hidden /> Belum pernah upload — masih bebas diubah
                    </span>
                  )}
                </Row>
              )}
              <Row label="Irama data">
                {CADENCE_LABEL[job.cadence_effective] || job.cadence_effective}
                {!job.cadence && <span className="text-gray-500"> (otomatis dari template)</span>}
              </Row>
              <Row label="Skema acuan DQ">
                {job.schema_baseline
                  ? `${job.schema_baseline.columns} kolom · sejak ${fmtDateTime(job.schema_baseline.updated_at)}`
                  : <span className="text-gray-500">Belum ada — upload S3 berikutnya menjadi acuan</span>}
              </Row>
              <Row label="Terakhir diubah">{job.updated_by || '–'} · {fmtDateTime(job.updated_at)}</Row>
            </dl>
          </Section>

          {job.notes && (
            <Section title="Catatan">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-700">{reflow(job.notes)}</p>
            </Section>
          )}

          <Section
            title="Query & parameter"
            action={(
              <button type="button" onClick={() => setShowQuery((s) => !s)} aria-expanded={showQuery} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline">
                {showQuery ? 'Sembunyikan' : 'Tampilkan'} <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showQuery ? 'rotate-180' : ''}`} aria-hidden />
              </button>
            )}
          >
            {showQuery ? (
              <div className="space-y-3">
                <pre className="max-h-80 overflow-auto rounded-lg bg-gray-900 px-4 py-3 font-mono text-xs leading-relaxed text-gray-300">{job.query.trim()}</pre>
                <pre className="max-h-80 overflow-auto rounded-lg bg-gray-900 px-4 py-3 font-mono text-xs leading-relaxed text-gray-300">{JSON.stringify(job.args, null, 2)}</pre>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Query SQL yang dijalankan dan parameter template job ini.</p>
            )}
          </Section>
        </div>

        <div className="space-y-6">
          <Section title="Riwayat perubahan" icon={History}>
            {job.history.length === 0 ? (
              <p className="text-sm text-gray-500">Belum ada riwayat.</p>
            ) : (
              <ol className="space-y-4 border-l border-gray-200 pl-1">
                {job.history.map((h) => <HistoryItem key={h.id} h={h} />)}
              </ol>
            )}
          </Section>

          <Section title="Run terakhir (Web UI)">
            {job.recent_runs.length === 0 ? (
              <p className="text-sm text-gray-500">Belum pernah dijalankan lewat Web UI.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {job.recent_runs.map((r) => (
                  <li key={`${r.run_id}-${r.target_date}`}>
                    <Link to={`/runs/${r.run_id}`} className="flex items-center justify-between gap-3 py-2 text-sm hover:text-brand-600">
                      <span className="text-gray-700">#{r.run_id} · {fmtDate(r.target_date)}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-xs tabular-nums text-gray-500">{fmtNumber(r.rows)}</span>
                        {r.dq_status && <DqBadge status={r.dq_status} />}
                        <StatusBadge status={r.status} />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>

      <Modal
        open={confirmToggle}
        onClose={() => !busy && setConfirmToggle(false)}
        size="md"
        title={job.enabled ? `Nonaktifkan ${job.name}?` : `Aktifkan ${job.name}?`}
        footer={(
          <>
            <Button variant="secondary" onClick={() => setConfirmToggle(false)} disabled={busy}>Batal</Button>
            <Button variant={job.enabled ? 'danger' : 'primary'} loading={busy} onClick={toggle}>{job.enabled ? 'Ya, nonaktifkan' : 'Ya, aktifkan'}</Button>
          </>
        )}
      >
        {actionError && <Alert tone="error" className="mb-4">{actionError}</Alert>}
        <p className="text-sm text-gray-600">
          {job.enabled
            ? 'Job tidak akan bisa dipilih di halaman Jalankan dan tidak ikut jadwal CLI lagi. Definisi & riwayatnya tetap tersimpan.'
            : job.name.startsWith('bronze_')
              ? 'Job bisa dijalankan dari Web UI, dan karena namanya diawali bronze_, otomatis ikut jadwal run_exporter.bat berikutnya.'
              : 'Job bisa dipilih di halaman Jalankan.'}
        </p>
      </Modal>
    </div>
  )
}
