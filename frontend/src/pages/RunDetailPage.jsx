import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Ban, Check, Copy, RotateCcw, Terminal } from 'lucide-react'
import { api } from '../lib/api'
import { hasRole, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtBytes, fmtDate, fmtDateRange, fmtDateTime, fmtDuration, fmtNumber, runElapsed, fmtCreator } from '../lib/format'
import {
  ACTIVE_STATUSES, Alert, Button, Card, FINAL_STATUSES, LoadingBlock, Modal, ProgressBar, Stat, StatusBadge,
} from '../components/ui'

const MAX_LOG_CHARS = 400_000

function LogPanel({ runId, finished }) {
  const [text, setText] = useState('')
  const [follow, setFollow] = useState(true)
  const offsetRef = useRef(0)
  const boxRef = useRef(null)

  useEffect(() => {
    offsetRef.current = 0
    setText('')
    let timer
    let stopped = false
    const tick = async () => {
      try {
        let more = true
        while (more && !stopped) {
          const r = await api(`/runs/${runId}/log?offset=${offsetRef.current}`)
          more = r.text.length > 0 && r.offset - offsetRef.current >= 256 * 1024 // potongan penuh -> ambil lagi
          if (r.text) setText((t) => (t + r.text).slice(-MAX_LOG_CHARS))
          offsetRef.current = r.offset
          if (r.finished && !r.text) stopped = true
        }
      } catch {
        // gagal sementara -- coba lagi di putaran berikutnya
      }
      if (!stopped) timer = setTimeout(tick, 2000)
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [runId])

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [text, follow])

  const onScroll = () => {
    const el = boxRef.current
    setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
  }

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <Terminal className="h-4 w-4 text-gray-400" aria-hidden /> Log
          {!finished && <span className="text-xs font-normal text-gray-500">· live</span>}
        </h2>
        {!follow && (
          <button type="button" onClick={() => setFollow(true)} className="text-xs font-medium text-brand-600 hover:underline">
            Ikuti log terbaru ↓
          </button>
        )}
      </div>
      <pre
        ref={boxRef}
        onScroll={onScroll}
        className="h-96 overflow-auto bg-gray-900 px-4 py-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap break-all text-gray-300"
      >
        {text || (finished ? 'Log tidak tersedia.' : 'Menunggu worker memulai run…')}
      </pre>
    </Card>
  )
}

function CopyButton({ value }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      title="Salin"
      aria-label="Salin"
      onClick={(e) => {
        e.stopPropagation()
        navigator.clipboard?.writeText(value).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1200)
        })
      }}
      className="shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
    >
      {done ? <Check className="h-3.5 w-3.5 text-brand-500" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
    </button>
  )
}

function Destination({ item, bucket }) {
  if (item.s3_key) {
    const uri = `s3://${bucket || '<bucket>'}/${item.s3_key}`
    return (
      <span className="flex items-center gap-1">
        <span className="truncate font-mono text-xs text-gray-600" title={uri}>{item.s3_key}</span>
        <CopyButton value={uri} />
      </span>
    )
  }
  if (item.location) return <span className="truncate font-mono text-xs text-gray-500" title={item.location}>{item.location}</span>
  return <span className="text-gray-400">–</span>
}

const ITEM_FILTERS = [
  ['all', 'Semua'],
  ['failed', 'Gagal'],
  ['success', 'Sukses'],
  ['running', 'Berjalan'],
  ['pending', 'Menunggu'],
  ['other', 'Lainnya'],
]

export default function RunDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { data: run, error, loading, reload } = useApi(`/runs/${id}`, {
    refreshMs: (r) => (r && !FINAL_STATUSES.includes(r.status) ? 2500 : null),
  })
  const { data: meta } = useApi('/runs/meta')
  const [filter, setFilter] = useState('all')
  const [confirm, setConfirm] = useState(null) // 'cancel' | 'retry'
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const items = run?.items || []
  const counts = useMemo(() => {
    const c = { all: items.length, other: 0 }
    for (const i of items) {
      c[i.status] = (c[i.status] || 0) + 1
      if (!['failed', 'success', 'running', 'pending'].includes(i.status)) c.other += 1
    }
    return c
  }, [items])
  const shown = items.filter((i) =>
    filter === 'all' ? true : filter === 'other' ? !['failed', 'success', 'running', 'pending'].includes(i.status) : i.status === filter)

  if (loading && !run) return <LoadingBlock />
  if (error && !run) {
    return (
      <div className="space-y-4">
        <Alert tone="error">{error.message}</Alert>
        <Link to="/runs" className="text-sm font-medium text-brand-600 hover:underline">← Kembali ke riwayat</Link>
      </div>
    )
  }

  const isOperator = hasRole(user, 'operator')
  const canCancel = isOperator && ACTIVE_STATUSES.includes(run.status) && !run.cancel_requested
  const canRetry = isOperator && FINAL_STATUSES.includes(run.status) && run.ok_items < run.total_items
  const finished = FINAL_STATUSES.includes(run.status)
  const o = run.options

  const doAction = async () => {
    setBusy(true)
    setActionError(null)
    try {
      if (confirm === 'cancel') {
        await api(`/runs/${run.id}/cancel`, { method: 'POST' })
        await reload()
      } else {
        const r = await api(`/runs/${run.id}/retry`, { method: 'POST' })
        navigate(`/runs/${r.id}`)
      }
      setConfirm(null)
    } catch (e) {
      setActionError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to="/runs" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Riwayat Run
        </Link>
        <div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-gray-800">Run #{run.id}</h1>
            <StatusBadge status={run.status} />
            {run.retry_of && (
              <Link to={`/runs/${run.retry_of}`} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-brand-600">
                <RotateCcw className="h-3.5 w-3.5" aria-hidden /> ulangan dari #{run.retry_of}
              </Link>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {canCancel && <Button variant="secondary" icon={Ban} onClick={() => setConfirm('cancel')}>Batalkan</Button>}
            {canRetry && <Button icon={RotateCcw} onClick={() => setConfirm('retry')}>Ulangi yang belum berhasil</Button>}
          </div>
        </div>
      </div>

      {run.cancel_requested && run.status === 'running' && (
        <Alert tone="warning" title="Pembatalan diminta">Worker akan berhenti setelah job yang sedang berjalan selesai.</Alert>
      )}
      {run.error && <Alert tone="error" title="Run berhenti karena error">{run.error}</Alert>}

      <Card className="p-5">
        <div className="grid grid-cols-2 gap-x-6 gap-y-5 lg:grid-cols-5">
          <div className="col-span-2 lg:col-span-1">
            <p className="text-xs font-medium text-gray-500">Progres</p>
            <div className="mt-2.5"><ProgressBar value={run.done_items} total={run.total_items} status={run.status} /></div>
            <p className="mt-1 text-xs text-gray-500">{run.ok_items} sukses · {run.failed_items} gagal</p>
          </div>
          <Stat label="Tanggal data" value={fmtDateRange(run.start_date, run.end_date)} sub={`${run.job_count} job`} />
          <Stat label="Total baris" value={fmtNumber(run.total_rows)} />
          <Stat label="Durasi" value={fmtDuration(runElapsed(run))} sub={run.started_at ? `mulai ${fmtDateTime(run.started_at)}` : 'belum mulai'} />
          <Stat label="Dibuat oleh" value={fmtCreator(run.created_by)} sub={fmtDateTime(run.created_at)} />
        </div>
        <div className="mt-5 flex flex-wrap gap-2 border-t border-gray-100 pt-4 text-xs">
          {[
            o.skip_s3 ? 'Upload S3: tidak' : 'Upload S3: ya',
            o.skip_local ? 'Simpan lokal: tidak' : 'Simpan lokal: ya',
            `Kompresi: ${o.compression}`,
            `Batch: ${fmtNumber(o.batch)}`,
            o.stop_on_error ? 'Berhenti saat gagal' : 'Lanjut saat gagal',
          ].map((t) => (
            <span key={t} className="rounded-md bg-gray-100 px-2 py-1 text-gray-600">{t}</span>
          ))}
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-gray-200 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-semibold text-gray-800">Rincian eksekusi</h2>
          <div className="-mx-1 flex gap-1 overflow-x-auto px-1" role="tablist">
            {ITEM_FILTERS.filter(([k]) => k === 'all' || counts[k]).map(([k, label]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={filter === k}
                onClick={() => setFilter(k)}
                className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  filter === k
                    ? 'bg-brand-600 text-white shadow-sm'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                {label} <span className="tabular-nums opacity-70">{counts[k]}</span>
              </button>
            ))}
          </div>
        </div>
        {/* Kartu: mobile */}
        <ul className="divide-y divide-gray-100 md:hidden">
          {shown.map((i) => (
            <li key={i.id} className="space-y-1.5 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-medium text-gray-800">{i.job_name}</span>
                <StatusBadge status={i.status} />
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums text-gray-500">
                <span>{fmtDate(i.target_date)}</span>
                <span>{fmtNumber(i.rows)} baris</span>
                <span>{fmtBytes(i.size_bytes)}</span>
                <span>{fmtDuration(i.elapsed_s)}</span>
              </div>
              {i.error && <p className="break-words font-mono text-xs text-red-600">{i.error}</p>}
              {(i.s3_key || i.location) && <Destination item={i} bucket={meta?.s3_bucket} />}
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto md:block">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50/80 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2.5">#</th>
                <th className="px-4 py-2.5">Job</th>
                <th className="px-4 py-2.5">Tanggal</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5 text-right">Baris</th>
                <th className="px-4 py-2.5 text-right">Ukuran</th>
                <th className="px-4 py-2.5 text-right">Durasi</th>
                <th className="px-4 py-2.5">Tujuan</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {shown.map((i) => (
                <tr key={i.id} className="align-top">
                  <td className="px-4 py-2.5 tabular-nums text-gray-400">{i.seq}</td>
                  <td className="px-4 py-2.5">
                    <p className="font-medium text-gray-800">{i.job_name}</p>
                    {i.error && <p className="mt-1 max-w-xl break-words font-mono text-xs text-red-600">{i.error}</p>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-gray-600">{fmtDate(i.target_date)}</td>
                  <td className="px-4 py-2.5"><StatusBadge status={i.status} /></td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-600">{fmtNumber(i.rows)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-gray-600">{fmtBytes(i.size_bytes)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-gray-600">{fmtDuration(i.elapsed_s)}</td>
                  <td className="max-w-xs px-4 py-2.5"><Destination item={i} bucket={meta?.s3_bucket} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <LogPanel runId={run.id} finished={finished} />

      <Modal
        open={!!confirm}
        onClose={() => !busy && setConfirm(null)}
        size="md"
        title={confirm === 'cancel' ? `Batalkan run #${run.id}?` : `Ulangi run #${run.id}?`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(null)} disabled={busy}>Tidak</Button>
            <Button variant={confirm === 'cancel' ? 'danger' : 'primary'} loading={busy} onClick={doAction}>
              {confirm === 'cancel' ? 'Ya, batalkan' : 'Ya, antrikan ulang'}
            </Button>
          </>
        }
      >
        {actionError && <Alert tone="error" className="mb-4">{actionError}</Alert>}
        {confirm === 'cancel' ? (
          <p className="text-sm text-gray-600">
            {run.status === 'queued'
              ? 'Run belum mulai dan akan langsung dibatalkan.'
              : 'Job yang sedang berjalan tetap diselesaikan (query tidak bisa dihentikan di tengah jalan); sisa job tidak dijalankan.'}
          </p>
        ) : (
          <p className="text-sm text-gray-600">
            Run baru dibuat untuk <strong>{run.total_items - run.ok_items}</strong> eksekusi yang belum berhasil, memakai definisi job
            <strong> terbaru</strong> dan opsi yang sama. Partisi S3 terkait akan ditimpa.
          </p>
        )}
      </Modal>
    </div>
  )
}
