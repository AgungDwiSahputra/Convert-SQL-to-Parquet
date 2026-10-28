import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Bell, HardDrive, KeyRound, Mail, RefreshCw, Send, ShieldCheck } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtRelative } from '../lib/format'
import { Alert, Badge, Button, Card, CardHeader, Field, LoadingBlock, PageHeader, Toggle, inputClass } from '../components/ui'

const SECURITY_LABEL = { none: 'tanpa enkripsi', starttls: 'STARTTLS', ssl: 'SSL/TLS' }
const KIND_LABEL = { run_failed: 'Run gagal', dq_warning: 'Peringatan DQ', anomaly: 'Anomali', stale: 'Data terlambat', test: 'Uji' }
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
  const [dqForm, setDqForm] = useState(null)
  const [dqSaving, setDqSaving] = useState(false)
  const [dqMessage, setDqMessage] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState(null)
  const [autoViewer, setAutoViewer] = useState(null)
  const [authMessage, setAuthMessage] = useState(null)

  useEffect(() => {
    if (data && !form) {
      setForm(data.notify)
      setRecipientsText(data.notify.recipients.join('\n'))
    }
    if (data && !dqForm) setDqForm(data.dq)
    if (data && autoViewer === null) setAutoViewer(data.auth.auto_viewer)
  }, [data, form, dqForm, autoViewer])

  if ((loading && !data) || !form || !dqForm) return error ? <Alert tone="error">{error.message}</Alert> : <LoadingBlock />
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  const setDq = (k, v) => setDqForm((f) => ({ ...f, [k]: v }))

  const saveDq = async () => {
    setDqSaving(true)
    setDqMessage(null)
    try {
      const { freshness_last: _last, s3inv_last: _inv, ...body } = dqForm
      await api('/settings/dq', { method: 'PUT', body })
      setDqMessage({ tone: 'info', text: 'Pengaturan data quality tersimpan.' })
    } catch (e) {
      setDqMessage({ tone: 'error', text: e.message })
    } finally {
      setDqSaving(false)
    }
  }

  const saveAuth = async (v) => {
    setAutoViewer(v)
    setAuthMessage(null)
    try {
      await api('/settings/auth', { method: 'PUT', body: { auto_viewer: v } })
      setAuthMessage({ tone: 'info', text: v ? 'Akun SSO baru otomatis masuk sebagai Viewer.' : 'Akun baru harus ditambahkan admin di halaman Users.' })
    } catch (e) {
      setAutoViewer(!v)
      setAuthMessage({ tone: 'error', text: e.message })
    }
  }

  const scanS3 = async () => {
    setScanning(true)
    try {
      setScanResult(await api('/settings/s3-inventory/scan', { method: 'POST' }))
    } catch (e) {
      setScanResult({ error: e.message, errors: [] })
    } finally {
      setScanning(false)
    }
  }
  const inv = scanResult || data.dq.s3inv_last

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
              description={`Dikirim worker setelah run selesai · nama pengirim "${data.smtp.from_name}" (SMTP_FROM_NAME di .env)`}
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
              <Toggle id="n-dq" label="Email peringatan data quality" description="Satu email ringkasan per run kalau ada peringatan DQ (kolom baru, volume menyimpang, selisih hitungan master). Run gagal: peringatan ikut di email run gagal." checked={form.on_dq_warning} onChange={(v) => set('on_dq_warning', v)} />
              <div className="flex justify-end">
                <Button loading={saving} onClick={save}>Simpan pengaturan</Button>
              </div>
            </div>
          </Card>

          <Card className="overflow-hidden">
            <CardHeader icon={ShieldCheck} title="Data quality" description="Ambang anomali volume & penjaga kesegaran data" />
            <div className="space-y-5 p-5">
              <Field label="Peringatan volume" hint="Jumlah baris dibanding median riwayat sesuai irama job. Hanya peringatan — tidak menahan upload.">
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-600">
                  <span>di bawah</span>
                  <input aria-label="Batas bawah persen" type="number" min={1} max={99} className={`${inputClass} max-w-20`} value={dqForm.volume_low_pct} onChange={(e) => setDq('volume_low_pct', Number(e.target.value))} />
                  <span>% atau di atas</span>
                  <input aria-label="Batas atas persen" type="number" min={101} max={1000} className={`${inputClass} max-w-24`} value={dqForm.volume_high_pct} onChange={(e) => setDq('volume_high_pct', Number(e.target.value))} />
                  <span>% median</span>
                </div>
              </Field>
              <Field label="Riwayat minimal" htmlFor="dq-min" hint="Jumlah tanggal riwayat sebelum volume job harian/snapshot mulai dinilai.">
                <div className="flex items-center gap-3">
                  <div className="w-24 shrink-0"><input id="dq-min" type="number" min={3} max={60} className={inputClass} value={dqForm.volume_min_history} onChange={(e) => setDq('volume_min_history', Number(e.target.value))} /></div>
                  <span className="text-sm text-gray-500">tanggal</span>
                </div>
              </Field>
              <Field label="Jangkauan cek celah" hint="Periode di tengah riwayat yang belum ter-load dilaporkan sejauh ini ke belakang, dimulai dari tanggal tertua yang tercatat pipeline atau ada di S3. Master tidak dicek.">
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-600">
                  <input aria-label="Hari (job harian)" type="number" min={7} max={366} className={`${inputClass} max-w-20`} value={dqForm.gap_days} onChange={(e) => setDq('gap_days', Number(e.target.value))} />
                  <span>hari ·</span>
                  <input aria-label="Minggu (job mingguan)" type="number" min={4} max={104} className={`${inputClass} max-w-20`} value={dqForm.gap_weeks} onChange={(e) => setDq('gap_weeks', Number(e.target.value))} />
                  <span>minggu ·</span>
                  <input aria-label="Bulan (job bulanan)" type="number" min={2} max={36} className={`${inputClass} max-w-20`} value={dqForm.gap_months} onChange={(e) => setDq('gap_months', Number(e.target.value))} />
                  <span>bulan</span>
                </div>
              </Field>
              <div className="space-y-3 rounded-xl border border-gray-200/80 p-4">
                <Toggle
                  id="inv-enabled"
                  label="Inventaris S3 harian"
                  description="Sekali sehari membaca daftar partisi di S3 (hanya baca) supaya kesegaran & celah juga mengenali data yang diunggah di luar catatan pipeline — mis. sebelum fitur data quality ada. Hari 0 baris tidak membuat file di S3, jadi celah bisa juga berarti memang kosong."
                  checked={dqForm.s3inv_enabled}
                  onChange={(v) => setDq('s3inv_enabled', v)}
                />
                {dqForm.s3inv_enabled && (
                  <Field label="Jam pindai (WIB)" htmlFor="inv-time" hint="Sebelum jam penjaga kesegaran.">
                    <input id="inv-time" type="time" className={`${inputClass} max-w-32`} value={dqForm.s3inv_scan_time} onChange={(e) => setDq('s3inv_scan_time', e.target.value)} />
                  </Field>
                )}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs text-gray-500">
                    {inv?.at
                      ? `Pindai terakhir ${fmtDateTime(inv.at)} (${inv.by}): ${inv.jobs} dari ${inv.targets} job, ${inv.dates} tanggal tercatat.`
                      : 'Belum pernah dipindai.'}
                  </p>
                  <Button variant="secondary" size="sm" icon={RefreshCw} loading={scanning} onClick={scanS3}>Pindai sekarang</Button>
                </div>
                {inv?.error && <Alert tone="error">{inv.error}</Alert>}
                {inv?.errors?.length > 0 && (
                  <Alert tone="warning" title={`${inv.errors.length} job gagal dipindai`}>
                    {inv.errors.slice(0, 5).map((e) => `${e.job_name}: ${e.error}`).join(' · ')}
                  </Alert>
                )}
              </div>
              <Toggle
                id="fr-enabled"
                label="Penjaga kesegaran harian"
                description="Sekali sehari memeriksa apakah tiap job aktif sudah memproses tanggal terbaru (harian/snapshot: H-1, mingguan: minggu lalu, bulanan: bulan lalu) dan apakah ada celah di tengah riwayat, lalu mengirim email ke penerima tetap. Aktifkan setelah jadwal pindah dari Task Scheduler ke aplikasi."
                checked={dqForm.freshness_enabled}
                onChange={(v) => setDq('freshness_enabled', v)}
              />
              {dqForm.freshness_enabled && (
                <Field label="Jam pemeriksaan (WIB)" htmlFor="fr-time" hint="Beri jeda setelah jadwal ekspor harian selesai.">
                  <input id="fr-time" type="time" className={`${inputClass} max-w-32`} value={dqForm.freshness_check_time} onChange={(e) => setDq('freshness_check_time', e.target.value)} />
                </Field>
              )}
              {data.dq.freshness_last && (
                <p className="text-xs text-gray-500">
                  Pemeriksaan terakhir {data.dq.freshness_last.date}: {data.dq.freshness_last.late.length} dari {data.dq.freshness_last.checked} job aktif belum segar, {(data.dq.freshness_last.gaps || []).length} job ada celah.
                </p>
              )}
              {dqMessage && <Alert tone={dqMessage.tone}>{dqMessage.text}</Alert>}
              <div className="flex justify-end">
                <Button loading={dqSaving} onClick={saveDq}>Simpan pengaturan DQ</Button>
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
            <CardHeader icon={KeyRound} title="Akses login" description="Siapa yang bisa masuk dengan akun SSO" />
            <div className="space-y-4 p-5">
              <Toggle
                id="auth-auto"
                label="Akun SSO baru otomatis Viewer"
                description="Siapa pun yang berhasil login SSO langsung masuk sebagai Viewer. Ekspor Cepat untuk akun ini baru bisa dipakai setelah admin menyetujuinya di halaman Users. Matikan supaya hanya akun yang ditambahkan admin yang bisa masuk (akun yang sudah terdaftar tetap bisa — nonaktifkan di Users)."
                checked={!!autoViewer}
                onChange={saveAuth}
              />
              {data.auth.pending_export > 0 && (
                <p className="text-sm text-gray-600">
                  <span className="font-semibold text-amber-700">{data.auth.pending_export} akun</span> menunggu persetujuan Ekspor Cepat ·{' '}
                  <Link to="/users" className="font-medium text-brand-700 hover:underline">Buka Users</Link>
                </p>
              )}
              {authMessage && <Alert tone={authMessage.tone}>{authMessage.text}</Alert>}
            </div>
          </Card>

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
