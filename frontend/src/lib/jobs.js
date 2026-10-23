// Pengetahuan bersama soal job untuk halaman Daftar Job, Detail, Form, dan Jalankan.

export const NO_MODULE = '(tanpa modul)'
export const TYPE_LABEL = { daily: 'Harian', weekly: 'Mingguan', master: 'Master' }

export function jobType(job) {
  if (!job.needs_date) return 'master'
  return job.s3?.layout === 'weekly' ? 'weekly' : 'daily'
}

// Field yang diterima tiap template (cermin signature di job_templates.py). Saat template
// berganti, field di luar daftar ini dibuang supaya args tetap bersih.
const BRONZE_COMMON = ['db', 'table', 'dataset', 'module', 'output', 'overrides', 'labels']
export const TEMPLATE_FIELDS = {
  bronze_daily: [...BRONZE_COMMON, 'date_col'],
  bronze_daily_strdate: [...BRONZE_COMMON, 'date_col'],
  bronze_daily_strdate_eq: [...BRONZE_COMMON, 'date_col', 'date_format'],
  bronze_daily_epoch_ms: [...BRONZE_COMMON, 'date_col'],
  bronze_daily_join: [...BRONZE_COMMON, 'date_col', 'alias', 'join_table', 'join_alias', 'join_on', 'join_db'],
  bronze_master: [...BRONZE_COMMON, 's3_file_name'],
  bronze_arcsde_view: ['linked_server', 'sde_db', 'sde_version', 'view', 'columns', 'dataset', 'module', 'output', 'overrides', 'labels'],
  sp_job: ['sp', 'function', 'output', 'needs_date', 'overrides', 'labels', 's3'],
  custom_sql: ['query', 'output', 'params', 'needs_date', 'overrides', 'labels', 's3'],
}

export const DAILY_TEMPLATES = ['bronze_daily', 'bronze_daily_strdate', 'bronze_daily_strdate_eq', 'bronze_daily_epoch_ms']
export const ADVANCED_TEMPLATES = ['sp_job', 'custom_sql', 'bronze_arcsde_view']

/** 'daily' | 'master' | 'join' untuk template bronze; null untuk template lanjutan. */
export function bronzeMode(template) {
  if (DAILY_TEMPLATES.includes(template)) return 'daily'
  if (template === 'bronze_master') return 'master'
  if (template === 'bronze_daily_join') return 'join'
  return null
}

export function pickArgs(args, template) {
  const allowed = TEMPLATE_FIELDS[template] || []
  const out = {}
  for (const k of allowed) {
    if (args[k] !== undefined) out[k] = args[k]
  }
  if (out.overrides && Object.keys(out.overrides).length === 0) delete out.overrides
  if (out.labels && out.labels.length === 0) delete out.labels
  return out
}

/** 'T_INV_StockItem' -> 'inv' (token setelah prefix T_/V_); 'SAP_FEED_GIS' -> 'sap' (token pertama). */
export function suggestModule(table) {
  const t = table || ''
  const m = /^(?:T|V|TBL|VW)_([A-Za-z0-9]+)_/i.exec(t) || /^([A-Za-z0-9]+)_/.exec(t)
  return m ? m[1].toLowerCase() : ''
}

export const STRDATE_LABEL = {
  '%d.%m.%Y': 'DD.MM.YYYY', '%d/%m/%Y': 'DD/MM/YYYY', '%d-%m-%Y': 'DD-MM-YYYY',
  '%Y%m%d': 'YYYYMMDD', '%Y/%m/%d': 'YYYY/MM/DD', '%Y-%m-%d': 'YYYY-MM-DD',
}

/** 'T_INV_StockItem' + 'inv' -> 'stock_item'; 'T_INV_TOrder' + 'inv' -> 't_order'. */
export function suggestDataset(table, module) {
  let s = (table || '').replace(/^(T|V|TBL|VW)_/i, '')
  if (module && s.toLowerCase().startsWith(`${module.toLowerCase()}_`)) s = s.slice(module.length + 1)
  return s
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
}

export function suggestName(module, dataset) {
  if (!dataset) return ''
  return `bronze_${module ? `${module}_` : ''}${dataset}`.toLowerCase()
}

export const HISTORY_LABEL = {
  import: 'Diimpor dari jobs.py',
  create: 'Dibuat',
  update: 'Diubah',
  enable: 'Diaktifkan',
  disable: 'Dinonaktifkan',
  s3_move: 'Path S3 dipindah',
}

const show = (v) => (v === undefined || v === null || v === '' ? '–' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** Perubahan antara dua snapshot riwayat -> [{field, from, to}]. */
export function diffSnapshots(before, after) {
  if (!before || !after) return []
  const out = []
  for (const k of ['template', 'enabled', 'notes']) {
    if (JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null)) {
      out.push({ field: k === 'notes' ? 'catatan' : k, from: k === 'notes' ? '…' : show(before[k]), to: k === 'notes' ? '(diubah)' : show(after[k]) })
    }
  }
  const a = before.args || {}
  const b = after.args || {}
  for (const k of [...new Set([...Object.keys(a), ...Object.keys(b)])]) {
    if (JSON.stringify(a[k] ?? null) !== JSON.stringify(b[k] ?? null)) out.push({ field: k, from: show(a[k]), to: show(b[k]) })
  }
  return out
}
