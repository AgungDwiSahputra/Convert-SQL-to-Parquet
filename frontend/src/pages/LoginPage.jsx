import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { Activity, Eye, EyeOff, LogIn, ShieldCheck, Wand2 } from 'lucide-react'
import { Alert, Button, Field, inputClass } from '../components/ui'
import { BrandMark } from '../components/Layout'
import { useAuth } from '../lib/auth'

const FEATURES = [
  [Wand2, 'Tambah job dari tabel SQL Server', 'Wizard memilih template & format tanggal otomatis — tanpa menulis kode.'],
  [Activity, 'Jalankan & pantau dari browser', 'Log live, riwayat lengkap, ulangi yang gagal dengan sekali klik.'],
  [ShieldCheck, 'Aman untuk data produksi', 'Uji coba selalu di-rollback, path S3 yang sudah dipakai terkunci.'],
]

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to={location.state?.from || '/'} replace />

  const submit = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
      navigate(location.state?.from || '/', { replace: true })
    } catch (err) {
      setError(err.message)
      setPassword('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen bg-gray-100">
      {/* Panel brand (desktop) */}
      <aside className="relative hidden w-[44%] max-w-2xl flex-col justify-between overflow-hidden bg-brand-900 p-12 lg:flex">
        <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-brand-700/40 blur-3xl" aria-hidden />
        <div className="pointer-events-none absolute -bottom-40 -left-24 h-96 w-96 rounded-full bg-brand-500/20 blur-3xl" aria-hidden />
        <div className="relative flex items-center gap-3">
          <BrandMark className="h-10 w-10" />
          <div className="leading-tight">
            <p className="font-semibold text-white">Parquet Exporter</p>
            <p className="text-xs text-brand-200/80">SQL Server → Parquet → S3</p>
          </div>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-white">
            Ekspor data ke datalake, cukup dari browser.
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-brand-100/80">
            Data monitoring air perkebunan dari SQL Server disalin ke Parquet dan S3 — siap untuk analitik dan AI/ML.
          </p>
          <ul className="mt-10 space-y-6">
            {FEATURES.map(([Icon, title, desc]) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-800 text-brand-200 ring-1 ring-brand-700">
                  <Icon className="h-5 w-5" aria-hidden />
                </span>
                <div>
                  <p className="text-sm font-semibold text-white">{title}</p>
                  <p className="mt-0.5 text-sm text-brand-200/80">{desc}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-brand-200/60">Akses diatur oleh admin aplikasi · login memakai akun SSO GIS Portal</p>
      </aside>

      {/* Form */}
      <main className="flex flex-1 items-center justify-center px-4 py-12 sm:px-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <img src="/favicon.svg" alt="" className="h-10 w-10" />
            <div className="leading-tight">
              <p className="font-semibold text-gray-800">Parquet Exporter</p>
              <p className="text-xs text-gray-500">SQL Server → Parquet → S3</p>
            </div>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-gray-800">Masuk</h1>
          <p className="mt-1 text-sm text-gray-500">Gunakan akun SSO GIS Portal Anda.</p>

          <form onSubmit={submit} className="mt-8 space-y-5">
            {error && <Alert tone="error">{error}</Alert>}
            <Field label="Username" htmlFor="username">
              <input
                id="username"
                className={inputClass}
                autoComplete="username"
                autoFocus
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="nama.pengguna"
              />
            </Field>
            <Field label="Password" htmlFor="password">
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  className={`${inputClass} pr-11`}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? 'Sembunyikan password' : 'Tampilkan password'}
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-gray-400 transition-colors hover:text-gray-600"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
                </button>
              </div>
            </Field>
            <Button type="submit" icon={LogIn} loading={submitting} className="h-11 w-full sm:h-11">
              Masuk
            </Button>
          </form>

          <p className="mt-8 text-center text-xs leading-relaxed text-gray-500">
            <ShieldCheck className="-mt-0.5 mr-1 inline h-3.5 w-3.5 text-brand-600" aria-hidden />
            Belum bisa masuk? Minta admin aplikasi menambahkan username Anda.
          </p>
        </div>
      </main>
    </div>
  )
}
