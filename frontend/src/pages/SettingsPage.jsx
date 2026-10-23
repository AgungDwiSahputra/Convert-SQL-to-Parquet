import { useEffect, useState } from 'react'
import { Bell, HardDrive, Mail, Send } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtRelative } from '../lib/format'
import { Alert, Badge, Button, Card, CardHeader, Field, LoadingBlock, PageHeader, Toggle, inputClass } from '../components/ui'

const SECURITY_LABEL = { none: 'tanpa enkripsi', starttls: 'STARTTLS', ssl: 'SSL/TLS' }
const KIND_LABEL = { run_failed: 'Run gagal', anomaly: 'Anomali', test: 'Uji' }
const STATUS_TONE = { sent: 'mint', failed: 'amber', skipped: 'gray' }
const STATUS_LABEL = { sent: 'Terkirim', failed: 'Gagal', skipped: 'Dilewati' }

export default function SettingsPage() {
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi('/settings')
  const [form, setForm] = useState(null)
  const [recipientsText, setRecipientsText] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)
  const [testTo, setTestTo] = useState(user.email || '')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    if (data && !form) {
      setForm(data.notify)
      setRecipientsText(data.notify.recipients.join('\n'))
    }
  }, [data, form])

  if ((loading && !data) || !form) return error ? <Alert tone="error">{error.message}</Alert> : <LoadingBlock />
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const recipients = recipientsText.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean)
      const r = await api('/settings/notify', { method: 'PUT', body: { ...form, recipients } })
      setRecipientsText(r.recipients.join('\n'))
      setMessage({ tone: 'info', text: 'Pengaturan notifikasi tersimpan.' })
    } catch (e) {
      setMessage({ tone: 'error', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  const sendTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api('/settings/test-email', { method: 'POST', body: { to: testTo } }))
      reload()
    } catch (e) {
      setTestResult({ status: 'failed', error: e.message })
    } finally {
      setTesting(false)
    }
  }

  const m = data.maintenance
  return (
    <>
      <PageHeader title="Pengaturan" description="Notifikasi email dan pemeliharaan otomatis. Akun SMTP diatur di file .env server, tidak di halaman ini." />
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <Card className="overflow-hidden">
            <CardHeader
              icon={Bell}
              title="Notifikasi email"
              description="Dikirim worker setelah run selesai"
              action={data.smtp.configured
                ? <Badge tone="mint">SMTP siap · {SECURITY_LABEL[data.smtp.security] || data.smtp.security}</Badge>
                : <Badge tone="amber">SMTP belum diatur</Badge>}
            />
            <div className="space-y-5 p-5">
              {!data.smtp.configured && (
                <Alert tone="warning" title="Email belum bisa dikirim">
                  Isi <span className="font-mono">SMTP_HOST</span>, <span className="font-mono">SMTP_PORT</span>, <span className="font-mono">SMTP_USER</span>,{' '}
                  <span className="font-mono">SMTP_PASSWORD</span>, <span className="font-mono">SMTP_FROM</span>, dan <span className="font-mono">SMTP_SECURITY</span> di file
                  <span className="font-mono"> .env</span> server, lalu restart web & worker.
                </Alert>
              )}
              {data.smtp.configured && !data.smtp.from_matches_login && (
                <Alert tone="warning" title="Alamat pengirim berbeda dengan akun login SMTP">
                  <span className="font-mono">SMTP_FROM</span> di .env tidak sama dengan <span className="font-mono">SMTP_USER</span>. Mail server biasanya tetap
                  menerima email (tercatat &quot;Terkirim&quot;) lalu membuangnya, atau penerima luar menolaknya. Samakan keduanya, atau kosongkan{' '}
                  <span className="font-mono">SMTP_FROM</span>, lalu restart web & worker.
                </Alert>
              )}
              {message && <Alert tone={message.tone}>{message.text}</Alert>}
              <Field label="Penerima tetap" htmlFor="recipients" hint="Satu alamat email per baris. Selalu menerima notifikasi.">
                <textarea id="recipients" rows={4} className={`${inputClass} font-mono`} value={recipientsText} onChange={(e) => setRecipientsText(e.target.value)} placeholder="tim.data@perusahaan.com" />
              </Field>
              <Toggle id="n-failed" label="Email saat run gagal" description="Run gagal atau terputus — berisi daftar job yang gagal & pesan error." checked={form.on_failed} onChange={(v) => set('on_failed', v)} />
              <Toggle id="n-creator" label="Kirim juga ke pembuat run" description="User yang menjalankan run ikut menerima email (run dari jadwal: penerima tetap saja)." checked={form.notify_creator} onChange={(v) => set('notify_creator', v)} />
              <Toggle id="n-anomaly" label="Email anomali 0 baris" description="Job yang biasanya berisi data tiba-tiba kosong beberapa tanggal berturut-turut. Dikirim sekali per kejadian." checked={form.on_anomaly} onChange={(v) => set('on_anomaly', v)} />
              {form.on_anomaly && (
                <Field label="Dianggap anomali setelah" htmlFor="n-streak">
                  <div className="flex items-center gap-3">
                    <div className="w-24 shrink-0"><input id="n-streak" type="number" min={2} max={14} className={inputClass} value={form.anomaly_streak} onChange={(e) => set('anomaly_streak', Number(e.target.value))} /></div>
                    <span className="text-sm text-gray-500">tanggal berturut-turut 0 baris</span>
                  </div>
                </Field>
              )}
              <div className="flex justify-end">
                <Button loading={saving} onClick={save}>Simpan pengaturan</Button>
              </div>
            </div>
          </Card>

          <Card className="overflow-hidden">
            <CardHeader icon={Mail} title="Riwayat notifikasi" description="30 terakhir" />
            {data.notifications.length === 0 ? (
              <p className="px-5 py-6 text-sm text-gray-500">Belum ada notifikasi.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {data.notifications.map((n) => (
                  <li key={n.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-800">{n.subject.replace('[Parquet Exporter] ', '')}</p>
                        <p className="mt-0.5 text-xs text-gray-500" title={fmtDateTime(n.created_at)}>
                          {KIND_LABEL[n.kind] || n.kind} · {n.recipients.length} penerima · {fmtRelative(n.created_at)}
                        </p>
                        {n.error && <p className="mt-1 break-words text-xs text-amber-700">{n.error}</p>}
                        {n.detail && <p className="mt-1 break-all font-mono text-[11px] text-gray-500" title="Balasan mail server — untuk message trace">Server: {n.detail}</p>}
                      </div>
                      <Badge tone={STATUS_TONE[n.status]}>{STATUS_LABEL[n.status] || n.status}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-6 lg:col-span-2">
          <Card className="overflow-hidden">
            <CardHeader icon={Send} title="Kirim email uji" description="Memastikan setelan SMTP benar" />
            <div className="space-y-4 p-5">
              <Field label="Kirim ke" htmlFor="test-to">
                <input id="test-to" type="email" className={inputClass} value={testTo} onChange={(e) => setTestTo(e.target.value)} />
              </Field>
              <Button variant="secondary" icon={Send} loading={testing} disabled={!testTo} onClick={sendTest} className="w-full">Kirim email uji</Button>
              {testResult && (
                testResult.status === 'sent'
                  ? (
                    <Alert tone="info" title={`Diterima mail server untuk ${testResult.to}`}>
                      Cek kotak masuk, folder spam/junk, dan karantina email. Kalau tidak sampai, berikan Message-ID di riwayat notifikasi ke tim IT untuk ditelusuri.
                    </Alert>
                  )
                  : <Alert tone="error" title={testResult.status === 'skipped' ? 'Tidak dikirim' : 'Gagal mengirim'}>{testResult.error}</Alert>
              )}
            </div>
          </Card>

          <Card className="overflow-hidden">
            <CardHeader icon={HardDrive} title="Pemeliharaan otomatis" description="Dijalankan worker sekali sehari" />
            <dl className="space-y-3 p-5 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-gray-500">Terakhir</dt>
                <dd className="text-right text-gray-800">{m.last?.date || 'Belum pernah (worker belum berjalan)'}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-gray-500">Backup database</dt>
                <dd className="text-right text-gray-800">disimpan {m.backup_keep} terakhir</dd>
              </div>
              {m.last?.backup && (
                <div>
                  <dt className="text-gray-500">File backup terakhir</dt>
                  <dd className="mt-0.5 break-all font-mono text-xs text-gray-600">{m.last.backup}</dd>
                </div>
              )}
              <div className="flex justify-between gap-4">
                <dt className="text-gray-500">Log run & worker</dt>
                <dd className="text-right text-gray-800">dihapus setelah {m.log_retention_days} hari</dd>
              </div>
            </dl>
          </Card>
        </div>
      </div>
    </>
  )
}
