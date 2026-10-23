// Semua waktu ditampilkan dalam WIB -- konsisten dengan asumsi WIB di pipeline.
const TZ = 'Asia/Jakarta'

export function fmtDateTime(iso) {
  if (!iso) return '–'
  return new Date(iso).toLocaleString('id-ID', {
    timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function fmtTime(iso) {
  if (!iso) return '–'
  return new Date(iso).toLocaleTimeString('id-ID', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** '2026-10-21' (tanggal data, tanpa jam) -> '21 Okt 2026' */
export function fmtDate(isoDate) {
  if (!isoDate) return '–'
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString('id-ID', {
    timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric',
  })
}

export function fmtDateRange(start, end) {
  return start === end ? fmtDate(start) : `${fmtDate(start)} – ${fmtDate(end)}`
}

export function fmtRelative(iso) {
  if (!iso) return '–'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 45) return 'baru saja'
  if (s < 3600) return `${Math.round(s / 60)} menit lalu`
  if (s < 86400) return `${Math.round(s / 3600)} jam lalu`
  if (s < 7 * 86400) return `${Math.round(s / 86400)} hari lalu`
  return fmtDateTime(iso)
}

export function fmtNumber(n) {
  return n === null || n === undefined ? '–' : Number(n).toLocaleString('id-ID')
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return '–'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toLocaleString('id-ID', { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '–'
  if (seconds < 60) return `${seconds.toLocaleString('id-ID', { maximumFractionDigits: 1 })} dtk`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m} mnt ${Math.round(seconds % 60)} dtk`
  return `${Math.floor(m / 60)} jam ${m % 60} mnt`
}

/**
 * Catatan job berasal dari komentar jobs.py yang dipotong manual per ~90 karakter. Gabungkan
 * baris lanjutan jadi paragraf; baris kosong, butir daftar (-, *, 1.), judul (---) dan baris
 * "tabel" (berindentasi + kolom rata dengan spasi berderet) tetap di barisnya sendiri.
 */
export function reflow(text) {
  if (!text) return text
  const out = []
  let prevBreak = true // baris sebelumnya kosong / tabel -> baris ini mulai blok baru
  for (const line of text.split('\n')) {
    const t = line.trim()
    const pre = /^\s{2,}\S/.test(line) && /\S\s{3,}\S/.test(t)
    const keep = !t || pre || /^([-*•]\s|\d+[.)]\s|---|#)/.test(t)
    if (!keep && !prevBreak && out.length) out[out.length - 1] += ` ${t}`
    else out.push(pre ? line.replace(/\s+$/, '') : t)
    prevBreak = !t || pre
  }
  return out.join('\n')
}

/** Waktu di masa depan -> 'dalam 3 jam'. */
export function fmtUntil(iso) {
  if (!iso) return '–'
  const s = (new Date(iso).getTime() - Date.now()) / 1000
  if (s < 60) return 'sebentar lagi'
  if (s < 3600) return `dalam ${Math.round(s / 60)} menit`
  if (s < 86400) return `dalam ${Math.round(s / 3600)} jam`
  return `dalam ${Math.round(s / 86400)} hari`
}

/** Pembuat run: 'jadwal:Nama' (dibuat scheduler) -> 'Jadwal · Nama'. */
export function fmtCreator(createdBy) {
  if (!createdBy) return '–'
  return createdBy.startsWith('jadwal:') ? `Jadwal · ${createdBy.slice(7)}` : createdBy
}

/** Durasi run: started_at -> finished_at (atau sekarang kalau masih berjalan). */
export function runElapsed(run) {
  if (!run?.started_at) return null
  const end = run.finished_at ? new Date(run.finished_at) : new Date()
  return (end - new Date(run.started_at)) / 1000
}
