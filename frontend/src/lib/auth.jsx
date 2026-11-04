import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, getPreview, setPreviewFlag } from './api'

const AuthContext = createContext(null)

const ROLE_RANK = { viewer: 1, operator: 2, admin: 3 }

export const ROLE_LABEL = { viewer: 'Viewer', operator: 'Operator', admin: 'Admin' }

export function hasRole(user, minimum) {
  return !!user && ROLE_RANK[user.role] >= ROLE_RANK[minimum]
}

/** Admin asli (juga saat pratinjau Viewer, di mana user.role = 'viewer') -- untuk tombol pratinjau. */
export function isRealAdmin(user) {
  return !!user && (user.real_role || user.role) === 'admin'
}

export function AuthProvider({ children }) {
  // undefined = masih mengecek session, null = belum login
  const [user, setUser] = useState(undefined)

  const refresh = useCallback(() => api('/auth/me').then(setUser, () => setUser(null)), [])

  useEffect(() => {
    refresh()
    const onExpired = () => setUser(null)
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [refresh])

  const login = useCallback(async (username, password) => {
    setPreviewFlag(false)
    setUser(await api('/auth/login', { method: 'POST', body: { username, password } }))
  }, [])

  const logout = useCallback(async () => {
    setPreviewFlag(false)
    try {
      await api('/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
    }
  }, [])

  /** Nyalakan/matikan pratinjau tampilan Viewer (admin). Role dibaca ulang dari server supaya menu & data ikut berubah. */
  const setPreview = useCallback(async (on) => {
    setPreviewFlag(on)
    await refresh()
  }, [refresh])

  return (
    <AuthContext.Provider value={{ user, login, logout, setPreview, preview: !!user?.preview && getPreview() }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
