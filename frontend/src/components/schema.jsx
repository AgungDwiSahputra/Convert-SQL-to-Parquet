// Pemilih database / tabel / kolom SQL Server (memakai /api/schema/*) -- dipakai form job & Ekspor Cepat.
import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { fmtNumber } from '../lib/format'
import { Badge, Field, Spinner, inputClass } from './ui'

const selectClass = `${inputClass} pr-8`
const enc = encodeURIComponent

export function Err({ children }) {
  return children ? <p className="text-xs text-red-600">{children}</p> : null
}

export function DbSelect({ id, label = 'Database', value, onChange, error }) {
  const { data, error: loadError } = useApi('/schema/databases')
  const dbs = data?.databases || []
  return (
    <Field label={label} htmlFor={id}>
      <select id={id} className={selectClass} value={value || ''} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">— pilih database —</option>
        {dbs.map((d) => <option key={d} value={d}>{d}</option>)}
        {value && data && !dbs.includes(value) && <option value={value}>{value}</option>}
      </select>
      <Err>{error || loadError?.message}</Err>
    </Field>
  )
}

export function TablePicker({ id, db, label = 'Tabel / view (schema dbo)', value, onChange, error }) {
  const { data, error: loadError, loading } = useApi(db ? `/schema/tables?db=${enc(db)}` : null)
  const [q, setQ] = useState('')
  const tables = data?.tables || []
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (s ? tables.filter((t) => t.name.toLowerCase().includes(s)) : tables).slice(0, 200)
  }, [tables, q])

  if (!db) return <Field label={label}><p className="text-sm text-gray-500">Pilih database dulu.</p></Field>
  return (
    <Field label={label} htmlFor={id}>
      {value ? (
        <div className="flex items-center justify-between gap-3 rounded-lg bg-brand-50 px-3 py-2.5">
          <span className="truncate font-mono text-sm font-medium text-brand-900">{value}</span>
          <button type="button" onClick={() => onChange(undefined)} className="shrink-0 text-xs font-medium text-brand-600 hover:underline">Ganti</button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
            <input id={id} type="search" className={`${inputClass} pl-9`} placeholder="Cari nama tabel…" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
          </div>
          <div className="max-h-64 overflow-y-auto rounded-lg border border-gray-200">
            {loading ? (
              <p className="flex items-center gap-2 p-3 text-sm text-gray-500"><Spinner /> Memuat daftar tabel…</p>
            ) : shown.length === 0 ? (
              <p className="p-3 text-sm text-gray-500">Tidak ada tabel yang cocok.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {shown.map((t) => (
                  <li key={t.name}>
                    <button type="button" onClick={() => onChange(t.name)} className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-gray-50">
                      <span className="truncate font-mono text-gray-700">{t.name}</span>
                      {t.type === 'view' && <Badge>view</Badge>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {tables.length > 200 && !q && <p className="text-xs text-gray-500">Menampilkan 200 dari {fmtNumber(tables.length)} — ketik untuk mencari.</p>}
        </div>
      )}
      <Err>{error || loadError?.message}</Err>
    </Field>
  )
}

export function ColumnSelect({ id, label, columns, value, onChange, filter = () => true, error, hint }) {
  const options = columns.filter(filter)
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <select id={id} className={selectClass} value={value || ''} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">— pilih kolom —</option>
        {options.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name} · {c.type}{c.leading_index ? ' · ber-index' : ''}
          </option>
        ))}
        {value && !options.some((c) => c.name === value) && <option value={value}>{value}</option>}
      </select>
      <Err>{error}</Err>
    </Field>
  )
}
