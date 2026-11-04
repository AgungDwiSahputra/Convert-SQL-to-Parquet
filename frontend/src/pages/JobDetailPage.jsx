import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, History, ListChecks, Lock, LockOpen, Pencil, Play, Power, PowerOff } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDate, fmtDateTime, fmtNumber, fmtRelative, reflow } from '../lib/format'
import { CADENCE_LABEL, HISTORY_LABEL, STRDATE_LABEL, TYPE_LABEL, diffSnapshots, jobType } from '../lib/jobs'
import {
  Alert, Badge, Button, Card, CardHeader, DqBadge, FreshnessBadge, GapBadge, LoadingBlock, Modal, StatusBadge, TechDetails,
} from '../components/ui'
import { EnabledBadge } from './JobsPage'

// SATU tampilan untuk semua peran: ringkasan sederhana + proses terakhir. "Detail teknis" (definisi, S3, skema acuan, catatan,
// query, riwayat perubahan, snapshot master) dan tombol kelola muncul bila server mengirim `tech` -- Operator/Admin.

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

/** Rincian teknis definisi job (dulu kartu "Ringkasan"). */
function TechSummary({ job, tech }) {
  return (
    <dl className="divide-y divide-gray-100">
      <Row label="Identifier"><span className="font-mono">{job.name}</span></Row>
      <Row label="Nama tampilan">{tech.display_name || <span className="text-gray-500">Otomatis ({job.label})</span>}</Row>
      <Row label="Template">{tech.template_label} <span className="font-mono text-xs text-gray-500">({tech.template})</span></Row>
      {tech.args?.table && <Row label="Sumber"><span className="font-mono">{tech.args.db}.dbo.{tech.args.table}</span></Row>}
      {tech.args?.date_col && <Row label="Kolom tanggal"><span className="font-mono">{tech.args.date_col}</span>{tech.args.date_format ? ` (format ${STRDATE_LABEL[tech.args.date_format] || tech.args.date_format})` : ''}</Row>}
      <Row label="File output"><span className="font-mono">{tech.output}</span></Row>
      <Row label="Folder S3">{tech.s3_folder ? <span className="font-mono">{tech.s3_folder}</span> : 'Tidak diupload ke S3'}</Row>
      {tech.s3_folder && (
        <Row label="Contoh path S3">
          <span className="break-all font-mono text-xs">{tech.s3_key_example || 'ditentukan dari data (partisi mingguan)'}</span>
        </Row>
      )}
      {tech.s3_folder && (
        <Row label="Kunci path S3">
          {tech.s3_locked_at ? (
            <span className="inline-flex items-center gap-1.5 text-amber-700">
              <Lock className="h-3.5 w-3.5" aria-hidden /> Terkunci sejak {fmtDate(tech.s3_locked_at.slice(0, 10))} — hanya admin yang bisa memindahkan
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
        {!tech.cadence && <span className="text-gray-500"> (otomatis dari template)</span>}
      </Row>
      <Row label="Skema acuan DQ">
        {tech.schema_baseline
          ? `${tech.schema_baseline.columns} kolom · sejak ${fmtDateTime(tech.schema_baseline.updated_at)}`
          : <span className="text-gray-500">Belum ada — upload S3 berikutnya menjadi acuan</span>}
      </Row>
      <Row label="Terakhir diubah">{tech.updated_by || '–'} · {fmtDateTime(tech.updated_at)}</Row>
    </dl>
  )
}

function SnapshotTable({ snapshots }) {
  if (snapshots.length === 0) return <p className="text-sm text-gray-500">Belum ada — dicatat mulai upload S3 berikutnya.</p>
  return (
    <>
      <p className="mb-3 text-xs text-gray-500">
        Folder baru hanya dibuat saat data berubah (hash SHA-256). Selama data sama, folder versi terbaru digeser ke tanggal proses terakhir —
        tanggal folder = terakhir terkonfirmasi.
      </p>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="py-2 pr-4">Folder</th>
              <th className="py-2 pr-4">Versi sejak</th>
              <th className="py-2 pr-4 text-right">Baris</th>
              <th className="py-2">Hash</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {snapshots.map((s) => (
              <tr key={s.id}>
                <td className="whitespace-nowrap py-2 pr-4 font-mono text-xs text-gray-700">snapshot_date={s.snapshot_date}</td>
                <td className="whitespace-nowrap py-2 pr-4 text-gray-600">{fmtDate(s.first_seen)}</td>
                <td className="py-2 pr-4 text-right tabular-nums text-gray-600">{fmtNumber(s.rows)}</td>
                <td className="py-2 font-mono text-xs text-gray-500" title={s.data_hash}>{s.data_hash.slice(0, 12)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function JobDetailPage() {
  const { name } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { data: job, error, loading, reload } = useApi(`/jobs/${encodeURIComponent(name)}`)
  const [confirmToggle, setConfirmToggle] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  if (loading && !job) return <LoadingBlock />
  if (error && !job) {
    return (
      <div className="space-y-4">
        <Alert tone="error">{error.status === 404 ? 'Data ini tidak ditemukan.' : error.message}</Alert>
        <Link to="/jobs" className="text-sm font-medium text-brand-600 hover:underline">← Kembali ke Daftar Data</Link>
      </div>
    )
  }

  const tech = job.tech // hanya Operator/Admin
  const t = jobType(job)
  const gaps = job.freshness?.gaps
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
          <ArrowLeft className="h-4 w-4" aria-hidden /> Daftar Data
        </Link>
        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-gray-800">{job.label}</h1>
              <EnabledBadge enabled={job.enabled} />
              <Badge tone="blue">{TYPE_LABEL[t]}</Badge>
            </div>
            {tech && <p className="mt-1 font-mono text-xs text-gray-400">{job.name}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            {tech?.can_edit && (
              <Button variant="secondary" icon={job.enabled ? PowerOff : Power} onClick={() => setConfirmToggle(true)}>
                {job.enabled ? 'Nonaktifkan' : 'Aktifkan'}
              </Button>
            )}
            {tech?.can_edit && (
              <Button variant="secondary" icon={Pencil} onClick={() => navigate(`/jobs/${encodeURIComponent(job.name)}/edit`)}>Edit</Button>
            )}
            {hasRole(user, 'operator') && tech && job.enabled && (
              <Button icon={Play} onClick={() => navigate(`/runs/new?jobs=${encodeURIComponent(job.name)}`)}>Jalankan</Button>
            )}
          </div>
        </div>
      </div>

      {tech && !tech.can_edit && hasRole(user, 'operator') && (
        <Alert tone="info">Data ini memakai template khusus admin — hanya admin yang bisa mengubah atau mengaktifkannya.</Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card className="overflow-hidden">
            <CardHeader icon={ListChecks} title="Ringkasan" />
            <dl className="divide-y divide-gray-100 px-5 py-1">
              {job.group && <Row label="Kelompok">{job.group}</Row>}
              <Row label="Frekuensi">{CADENCE_LABEL[job.cadence_effective] || job.cadence_effective}</Row>
              {job.enabled && job.freshness && (
                <Row label="Kesegaran">
                  <span className="inline-flex flex-wrap items-center gap-2">
                    <FreshnessBadge freshness={job.freshness} />
                    <span className="text-gray-600">
                      data terakhir {job.freshness.latest ? fmtDate(job.freshness.latest) : 'belum ada'} · seharusnya sudah {fmtDate(job.freshness.expected)}
                    </span>
                  </span>
                </Row>
              )}
              {job.enabled && gaps && (
                <Row label="Tanggal belum masuk">
                  {gaps.count === 0 ? (
                    <span className="text-gray-600">Tidak ada — semua periode sudah masuk</span>
                  ) : (
                    <div className="space-y-1.5">
                      <GapBadge gaps={gaps} />
                      <ul className="space-y-0.5 text-sm text-amber-800">
                        {gaps.items.map((g) => <li key={g.start}>{g.label}</li>)}
                      </ul>
                      {gaps.count > gaps.items.length && <p className="text-xs text-gray-500">+{gaps.count - gaps.items.length} periode lain</p>}
                      <p className="text-xs text-gray-500">Belum tercatat masuk ke datalake — bisa juga memang tidak ada data pada periode itu.</p>
                    </div>
                  )}
                </Row>
              )}
              {!job.enabled && <Row label="Status"><span className="text-gray-600">Tidak aktif — data ini tidak diproses lagi.</span></Row>}
            </dl>
            {tech && (
              <TechDetails storageKey="job-tech" summary="identifier, template, sumber, file & path S3, skema acuan">
                <TechSummary job={job} tech={tech} />
              </TechDetails>
            )}
            {tech?.notes && (
              <TechDetails storageKey="job-notes" title="Detail teknis · Catatan" summary="catatan operator/admin">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-700">{reflow(tech.notes)}</p>
              </TechDetails>
            )}
            {tech && (
              <TechDetails storageKey="job-query" title="Detail teknis · Query & parameter" summary="query SQL dan argumen template">
                <div className="space-y-3">
                  <pre className="max-h-80 overflow-auto rounded-lg bg-gray-900 px-4 py-3 font-mono text-xs leading-relaxed text-gray-300">{tech.query.trim()}</pre>
                  <pre className="max-h-80 overflow-auto rounded-lg bg-gray-900 px-4 py-3 font-mono text-xs leading-relaxed text-gray-300">{JSON.stringify(tech.args, null, 2)}</pre>
                </div>
              </TechDetails>
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="overflow-hidden">
            <CardHeader icon={History} title="Proses terakhir" />
            {job.recent_runs.length === 0 ? (
              <p className="px-5 py-4 text-sm text-gray-500">Belum pernah diproses lewat aplikasi ini.</p>
            ) : (
              <ul className="divide-y divide-gray-100 px-5">
                {job.recent_runs.map((r) => (
                  <li key={`${r.run_id}-${r.target_date}`}>
                    <Link to={`/runs/${r.run_id}`} className="flex items-center justify-between gap-3 py-2.5 text-sm hover:text-brand-600">
                      <span className="whitespace-nowrap text-gray-700">No. {r.run_id} · {fmtDate(r.target_date)}</span>
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
            {tech && (
              <TechDetails storageKey="job-history" title="Detail teknis · Riwayat perubahan" summary={`${tech.history.length} perubahan`}>
                {tech.history.length === 0 ? (
                  <p className="text-sm text-gray-500">Belum ada riwayat.</p>
                ) : (
                  <ol className="space-y-4 border-l border-gray-200 pl-1">
                    {tech.history.map((h) => <HistoryItem key={h.id} h={h} />)}
                  </ol>
                )}
              </TechDetails>
            )}
            {tech?.snapshots && (
              <TechDetails storageKey="job-snapshots" title="Detail teknis · Snapshot master" summary="versi data master di S3">
                <SnapshotTable snapshots={tech.snapshots} />
              </TechDetails>
            )}
          </Card>
        </div>
      </div>

      <Modal
        open={confirmToggle}
        onClose={() => !busy && setConfirmToggle(false)}
        size="md"
        title={job.enabled ? `Nonaktifkan ${job.label}?` : `Aktifkan ${job.label}?`}
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
            ? 'Data ini tidak bisa dipilih di halaman Jalankan Proses dan tidak ikut jadwal CLI lagi. Definisi & riwayatnya tetap tersimpan.'
            : job.name.startsWith('bronze_')
              ? 'Data bisa diproses dari aplikasi, dan karena identifier-nya diawali bronze_, otomatis ikut jadwal run_exporter.bat berikutnya.'
              : 'Data bisa dipilih di halaman Jalankan Proses.'}
        </p>
      </Modal>
    </div>
  )
}
