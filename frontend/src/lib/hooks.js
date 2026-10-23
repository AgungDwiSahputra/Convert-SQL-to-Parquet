import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

/**
 * GET `path` + polling opsional. path null/'' = tidak memuat apa pun (mis. menunggu pilihan user).
 * refreshMs: angka (ms), atau fungsi (data) => ms|null -- null = berhenti polling
 * (mis. berhenti saat run sudah selesai).
 */
export function useApi(path, { refreshMs = null } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(!!path)
  const refreshRef = useRef(refreshMs)
  refreshRef.current = refreshMs

  const load = useCallback(async () => {
    if (!path) return null
    try {
      const result = await api(path)
      setData(result)
      setError(null)
      return result
    } catch (e) {
      setError(e)
      return null
    } finally {
      setLoading(false)
    }
  }, [path])

  useEffect(() => {
    if (!path) {
      setData(null)
      setError(null)
      setLoading(false)
      return undefined
    }
    let timer
    let cancelled = false
    setLoading(true)
    const tick = async () => {
      const result = await load()
      if (cancelled) return
      const r = refreshRef.current
      const ms = typeof r === 'function' ? r(result) : r
      if (ms) timer = setTimeout(tick, ms)
    }
    tick()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [load, path])

  return { data, error, loading, reload: load }
}

/** Nilai yang baru berubah setelah `ms` tanpa perubahan lagi (mis. validasi live saat mengetik). */
export function useDebounced(value, ms = 450) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}
