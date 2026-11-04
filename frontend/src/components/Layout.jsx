import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router'
import {
  BookOpen, CalendarClock, Database, Download, Eye, EyeOff, History, LayoutDashboard, LogOut, Menu, Play, Settings, Users, X,
} from 'lucide-react'
import { ROLE_LABEL, hasRole, isRealAdmin, useAuth } from '../lib/auth'
import { useApi } from '../lib/hooks'

// Istilah sederhana untuk SEMUA peran (satu tampilan): Run -> Proses, Job -> Data, Ekspor Cepat -> Unduh Data.
const NAV = [
  { to: '/', label: 'Beranda', icon: LayoutDashboard, match: (p) => p === '/' },
  { to: '/runs/new', label: 'Jalankan Proses', icon: Play, role: 'operator', match: (p) => p === '/runs/new' },
  { to: '/runs', label: 'Riwayat Proses', icon: History, match: (p) => p.startsWith('/runs') && p !== '/runs/new' },
  { to: '/schedules', label: 'Jadwal', icon: CalendarClock, match: (p) => p.startsWith('/schedules') },
  { to: '/jobs', label: 'Daftar Data', icon: Database, match: (p) => p.startsWith('/jobs') },
  { to: '/kb', label: 'Knowledge Base', icon: BookOpen, match: (p) => p.startsWith('/kb') },
  { to: '/export', label: 'Unduh Data', icon: Download, match: (p) => p.startsWith('/export') },
  { section: 'Admin', role: 'admin' },
  { to: '/users', label: 'Users', icon: Users, role: 'admin', match: (p) => p.startsWith('/users') },
  { to: '/settings', label: 'Pengaturan', icon: Settings, role: 'admin', match: (p) => p.startsWith('/settings') },
]

export function BrandMark({ className = 'h-9 w-9' }) {
  return (
    <span className={`flex shrink-0 items-center justify-center rounded-xl bg-white shadow-sm ${className}`}>
      <svg viewBox="0 0 32 32" className="h-[60%] w-[60%]" aria-hidden>
        <g fill="none" stroke="#059669" strokeWidth="3" strokeLinecap="round">
          <path d="M6 9h20M6 16h20M6 23h12" />
        </g>
      </svg>
    </span>
  )
}

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-3 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400">
      <BrandMark />
      <div className="leading-tight">
        <p className="text-sm font-semibold text-white">Monitoring Data</p>
        <p className="text-xs text-brand-200/80">Datalake &amp; Configuration Visibility</p>
      </div>
    </Link>
  )
}

function WorkerStatus() {
  const { data } = useApi('/runs/meta', { refreshMs: 15000 })
  if (!data) return null
  const online = data.worker_online
  return (
    <div
      className="flex items-center gap-2.5 rounded-lg bg-brand-950/30 px-3 py-2.5 text-xs text-brand-100"
      title={online ? 'Proses otomatis (worker) berjalan: jadwal & permintaan diproses.' : 'Proses otomatis (worker) berhenti: jadwal & permintaan tertahan di antrian.'}
    >
      <span className="relative flex h-2 w-2">
        {online && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-60" />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${online ? 'bg-brand-400' : 'bg-amber-400'}`} />
      </span>
      Proses otomatis {online ? 'aktif' : 'berhenti'}
    </div>
  )
}

function NavLinks({ user, pathname, onNavigate }) {
  return (
    <nav className="space-y-1" aria-label="Navigasi utama">
      {NAV.filter((n) => !n.role || hasRole(user, n.role)).map(({ to, label, icon: Icon, match, section }) => {
        if (section) {
          return <p key={section} className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-brand-200/60">{section}</p>
        }
        const active = match(pathname)
        return (
          <Link
            key={to}
            to={to}
            onClick={onNavigate}
            aria-current={active ? 'page' : undefined}
            className={`flex h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors duration-150 lg:h-10 ${
              active
                ? 'bg-brand-500 text-white shadow-sm shadow-brand-950/30'
                : 'text-brand-200 hover:bg-brand-700/60 hover:text-white'
            }`}
          >
            <Icon className="h-[18px] w-[18px]" aria-hidden />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}

function PreviewButton({ preview, onToggle }) {
  const Icon = preview ? EyeOff : Eye
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center gap-2.5 rounded-lg bg-brand-950/30 px-3 py-2.5 text-xs font-medium text-brand-100 transition-colors hover:bg-brand-700/60 hover:text-white"
      title={preview ? 'Kembali ke tampilan Admin' : 'Lihat aplikasi persis seperti pengguna Viewer (data & menu Viewer)'}
    >
      <Icon className="h-4 w-4" aria-hidden />
      {preview ? 'Keluar pratinjau Viewer' : 'Pratinjau tampilan Viewer'}
    </button>
  )
}

function PreviewBanner({ onExit }) {
  return (
    <div className="mb-6 flex flex-col gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900 sm:flex-row sm:items-center sm:justify-between">
      <p className="flex items-center gap-2">
        <Eye className="h-4 w-4 shrink-0" aria-hidden />
        <span><span className="font-semibold">Pratinjau tampilan Viewer.</span> Menu, data, dan tombol tampil persis seperti untuk Viewer.</span>
      </p>
      <button type="button" onClick={onExit} className="shrink-0 self-start rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-800 hover:bg-blue-100 sm:self-auto">
        Kembali ke tampilan Admin
      </button>
    </div>
  )
}

function UserBlock({ user, logout }) {
  const initials = (user.full_name || user.username).split(/[\s.]+/).map((s) => s[0]).slice(0, 2).join('').toUpperCase()
  return (
    <div className="flex items-center gap-3 rounded-xl bg-brand-950/30 p-2.5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-500 text-xs font-semibold text-white">
        {initials}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-white">{user.full_name || user.username}</p>
        <p className="truncate text-xs text-brand-200/80">{user.preview ? 'Admin · pratinjau Viewer' : ROLE_LABEL[user.role]}</p>
      </div>
      <button
        type="button"
        onClick={logout}
        aria-label="Keluar"
        title="Keluar"
        className="rounded-lg p-2 text-brand-200 transition-colors hover:bg-brand-700/60 hover:text-white"
      >
        <LogOut className="h-[18px] w-[18px]" aria-hidden />
      </button>
    </div>
  )
}

export default function Layout() {
  const { user, logout, setPreview } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => setMenuOpen(false), [pathname])
  const canPreview = isRealAdmin(user)
  // Ganti peran (pratinjau) -> mulai dari Beranda: halaman admin (Users/Pengaturan) tidak tersedia untuk Viewer.
  const togglePreview = async () => {
    await setPreview(!user.preview)
    navigate('/')
  }

  return (
    <div className="min-h-screen lg:flex">
      {/* Sidebar desktop */}
      <aside className="hidden w-64 shrink-0 bg-brand-900 lg:fixed lg:inset-y-0 lg:flex lg:flex-col">
        <div className="flex h-[72px] items-center px-5">
          <Brand />
        </div>
        <div className="flex flex-1 flex-col justify-between overflow-y-auto px-3 pb-4">
          <div>
            <p className="px-3 pb-2 pt-2 text-[11px] font-semibold uppercase tracking-wider text-brand-200/60">Menu</p>
            <NavLinks user={user} pathname={pathname} />
          </div>
          <div className="space-y-2">
            <WorkerStatus />
            {canPreview && <PreviewButton preview={!!user.preview} onToggle={togglePreview} />}
            <UserBlock user={user} logout={logout} />
          </div>
        </div>
      </aside>

      {/* Top bar mobile/tablet */}
      <header className="sticky top-0 z-40 bg-brand-900 shadow-sm lg:hidden">
        <div className="flex h-16 items-center justify-between px-4 sm:px-6">
          <Brand />
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            aria-expanded={menuOpen}
            aria-label={menuOpen ? 'Tutup menu' : 'Buka menu'}
            className="rounded-lg p-2.5 text-brand-100 transition-colors hover:bg-brand-700/60"
          >
            {menuOpen ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}
          </button>
        </div>
        {menuOpen && (
          <div className="space-y-3 border-t border-brand-800 px-4 pb-4 pt-3 sm:px-6">
            <NavLinks user={user} pathname={pathname} onNavigate={() => setMenuOpen(false)} />
            <WorkerStatus />
            {canPreview && <PreviewButton preview={!!user.preview} onToggle={togglePreview} />}
            <UserBlock user={user} logout={logout} />
          </div>
        )}
      </header>

      <main className="min-w-0 flex-1 lg:pl-64">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
          {user.preview && <PreviewBanner onExit={togglePreview} />}
          {/* key = peran efektif: masuk/keluar pratinjau memasang ulang halaman -> data dimuat ulang sesuai peran */}
          <Outlet key={user.role} />
        </div>
      </main>
    </div>
  )
}
