import { useState } from 'react'
import { UserPlus, Users } from 'lucide-react'
import { api } from '../lib/api'
import { ROLE_LABEL, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'
import { fmtDateTime, fmtRelative } from '../lib/format'
import { Alert, Button, Card, EmptyState, Field, LoadingBlock, PageHeader, inputClass } from '../components/ui'

const ROLES = [
  ['viewer', 'Melihat daftar job, riwayat run, dan log.'],
  ['operator', 'Viewer + menjalankan, membatalkan, dan mengulang run.'],
  ['admin', 'Operator + mengelola user aplikasi.'],
]
const selectClass = `${inputClass} pr-8`

function ActiveSwitch({ active, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!active)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50 ${active ? 'bg-brand-500' : 'bg-gray-300'}`}
    >
      <span className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform duration-200 ${active ? 'translate-x-5' : 'translate-x-0.5'}`} />
    </button>
  )
}

export default function UsersPage() {
  const { user: me } = useAuth()
  const { data, error, loading, reload } = useApi('/users')
  const [username, setUsername] = useState('')
  const [role, setRole] = useState('viewer')
  const [adding, setAdding] = useState(false)
  const [busyUser, setBusyUser] = useState(null)
  const [message, setMessage] = useState(null) // {tone, text}
  const users = data?.users || []

  const add = async (e) => {
    e.preventDefault()
    setAdding(true)
    setMessage(null)
    try {
      const r = await api('/users', { method: 'POST', body: { username, role } })
      setMessage({ tone: 'info', text: `User ${r.username} ditambahkan sebagai ${ROLE_LABEL[r.role]}. Ia sudah bisa login dengan akun SSO-nya.` })
      setUsername('')
      await reload()
    } catch (err) {
      setMessage({ tone: 'error', text: err.message })
    } finally {
      setAdding(false)
    }
  }

  const update = async (u, patch) => {
    setBusyUser(u.username)
    setMessage(null)
    try {
      await api(`/users/${encodeURIComponent(u.username)}`, { method: 'PATCH', body: patch })
      await reload()
    } catch (err) {
      setMessage({ tone: 'error', text: err.message })
    } finally {
      setBusyUser(null)
    }
  }

  return (
    <>
      <PageHeader title="Users" description="Siapa yang boleh masuk ke aplikasi ini dan apa role-nya. Login tetap memakai akun SSO masing-masing." />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="h-fit p-4 sm:p-5">
          <h2 className="text-sm font-semibold text-gray-800">Tambah user</h2>
          <form onSubmit={add} className="mt-4 space-y-4">
            <Field label="Username SSO" htmlFor="new-username" hint="Sama persis dengan username login SSO, mis. nama.pengguna">
              <input id="new-username" className={inputClass} required value={username} onChange={(e) => setUsername(e.target.value)} placeholder="nama.pengguna" autoComplete="off" />
            </Field>
            <Field label="Role" htmlFor="new-role">
              <select id="new-role" className={selectClass} value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map(([r]) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
              </select>
            </Field>
            <Button type="submit" icon={UserPlus} loading={adding} className="w-full">Tambah</Button>
          </form>
          <dl className="mt-6 space-y-3 border-t border-gray-100 pt-4 text-xs">
            {ROLES.map(([r, desc]) => (
              <div key={r}>
                <dt className="font-medium text-gray-700">{ROLE_LABEL[r]}</dt>
                <dd className="text-gray-500">{desc}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          {message && <Alert tone={message.tone}>{message.text}</Alert>}
          {error && <Alert tone="error">{error.message}</Alert>}
          <Card className="overflow-hidden">
            {loading && !data ? (
              <LoadingBlock />
            ) : users.length === 0 ? (
              <EmptyState icon={Users} title="Belum ada user" />
            ) : (
              <ul className="divide-y divide-gray-100">
                {users.map((u) => (
                  <li key={u.username} className={`flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between ${u.active ? '' : 'opacity-60'}`}>
                    <div className="min-w-0">
                      <p className="truncate font-medium text-gray-800">
                        {u.full_name || u.username}
                        {u.username === me.username && <span className="ml-2 text-xs font-normal text-brand-600">(Anda)</span>}
                      </p>
                      <p className="truncate text-xs text-gray-500">
                        {u.username}{u.email ? ` · ${u.email}` : ''}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-400" title={u.last_login_at ? fmtDateTime(u.last_login_at) : undefined}>
                        {u.last_login_at ? `Login terakhir ${fmtRelative(u.last_login_at)}` : 'Belum pernah login'} · ditambahkan {u.created_by || '–'}
                      </p>
                    </div>
                    <div className="flex items-center gap-4">
                      <select
                        aria-label={`Role ${u.username}`}
                        className={`${selectClass} w-36`}
                        value={u.role}
                        disabled={busyUser === u.username}
                        onChange={(e) => update(u, { role: e.target.value })}
                      >
                        {ROLES.map(([r]) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                      </select>
                      <ActiveSwitch
                        active={u.active}
                        disabled={busyUser === u.username}
                        label={`${u.active ? 'Nonaktifkan' : 'Aktifkan'} ${u.username}`}
                        onChange={(v) => update(u, { active: v })}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
