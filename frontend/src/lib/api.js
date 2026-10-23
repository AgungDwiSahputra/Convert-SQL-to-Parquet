// Pembungkus fetch ke backend FastAPI (/api/...). Session = cookie HttpOnly, dikirim otomatis.

export class ApiError extends Error {
  constructor(status, message, detail = null) {
    super(message)
    this.status = status
    this.detail = detail // mis. {message, errors: {field: pesan}} dari validasi job (HTTP 422)
  }
}

function detailMessage(detail) {
  if (!detail) return null
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object' && !Array.isArray(detail) && detail.message) return detail.message
  // Error validasi FastAPI (422): [{loc: [...], msg: '...'}]
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).filter((l) => l !== 'body').join('.')}: ${d.msg}`).join('; ')
  }
  return JSON.stringify(detail)
}

export async function api(path, { method = 'GET', body } = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'Tidak bisa menghubungi server. Periksa koneksi jaringan.')
  }
  let data = null
  try {
    data = await res.json()
  } catch {
    // respons tanpa body JSON
  }
  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.dispatchEvent(new Event('auth:expired'))
  }
  if (!res.ok) {
    throw new ApiError(res.status, detailMessage(data?.detail) || `Permintaan gagal (HTTP ${res.status}).`, data?.detail ?? null)
  }
  return data
}

/** POST yang mengembalikan FILE (ekspor .parquet): simpan lewat browser, return info dari header. */
export async function apiDownload(path, body) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Tidak bisa menghubungi server. Periksa koneksi jaringan.')
  }
  if (!res.ok) {
    let data = null
    try {
      data = await res.json()
    } catch {
      // bukan JSON
    }
    if (res.status === 401) window.dispatchEvent(new Event('auth:expired'))
    throw new ApiError(res.status, detailMessage(data?.detail) || `Permintaan gagal (HTTP ${res.status}).`, data?.detail ?? null)
  }
  const blob = await res.blob()
  const name = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') || '')?.[1] || 'export.parquet'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
  return {
    name,
    size: blob.size,
    rows: Number(res.headers.get('X-Rows')),
    truncated: res.headers.get('X-Truncated') === '1',
    maxRows: Number(res.headers.get('X-Max-Rows')),
    elapsed: Number(res.headers.get('X-Elapsed')),
  }
}
