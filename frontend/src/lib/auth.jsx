import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from './api'

const AuthContext = createContext(null)

const ROLE_RANK = { viewer: 1, operator: 2, admin: 3 }

export const ROLE_LABEL = { viewer: 'Viewer', operator: 'Operator', admin: 'Admin' }

export function hasRole(user, minimum) {
  return !!user && ROLE_RANK[user.role] >= ROLE_RANK[minimum]
}

export function AuthProvider({ children }) {
  // undefined = masih mengecek session, null = belum login
  const [user, setUser] = useState(undefined)

  useEffect(() => {
    api('/auth/me').then(setUser, () => setUser(null))
    const onExpired = () => setUser(null)
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [])

  const login = useCallback(async (username, password) => {
    setUser(await api('/auth/login', { method: 'POST', body: { username, password } }))
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
    }
  }, [])

  return <AuthContext.Provider value={{ user, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
