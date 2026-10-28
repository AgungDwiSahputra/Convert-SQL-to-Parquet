"""
NOTIFIKASI EMAIL -- dikirim worker.py setelah run selesai.

  run_failed : run gagal / terputus -> penerima tetap (+ pembuat run kalau setelan aktif). Peringatan
               data quality run itu ikut dicantumkan (satu email, bukan dua).
  dq_warning : run TIDAK gagal tapi ada peringatan data quality (pipeline/dq.py: kolom baru, volume
               menyimpang, selisih hitungan pada master, ...) -> SATU email ringkasan per run.
  anomaly    : job berirama HARIAN yang PERNAH berisi data, tapi 0 baris pada N tanggal terakhir
               berturut-turut (kasus kegagalan senyap, mis. filter tanggal salah format). Dikirim SEKALI
               per kejadian: tidak dikirim ulang sampai job itu kembali berisi data lalu kosong lagi.
               Riwayat dari dq_results (run CLI ikut terhitung).
  stale      : penjaga kesegaran harian (freshness.py): job aktif yang data terbarunya terlambat / belum ada,
               atau punya CELAH (tanggal / minggu / bulan di tengah riwayat yang belum ter-load).
  test       : email uji dari halaman Pengaturan.

Daftar penerima & saklar di tabel settings (non-rahasia, lihat app_settings.py). Akun SMTP HANYA
dari environment/.env (config.py). Gagal kirim TIDAK pernah menggagalkan run -- semua percobaan
tercatat di tabel notifications.
"""

from __future__ import annotations

import html
import json
import re
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from pipeline import dq, run_store
from pipeline.app_settings import get_settings
from pipeline.config import (
    APP_BASE_URL, SMTP_FROM, SMTP_FROM_NAME, SMTP_HOST, SMTP_LOCAL_HOSTNAME, SMTP_PASSWORD, SMTP_PORT, SMTP_SECURITY,
    SMTP_USER,
)
from pipeline.job_store import dq_latest_rows, utc_now
from pipeline.scheduler import WIB

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Prefix subjek sengaja tidak diubah: penerima mungkin sudah punya aturan filter kotak masuk berdasarkan prefix ini.
SUBJECT_PREFIX = "[Parquet Exporter]"
APP_NAME = "Parquet Exporter"
DEFAULT_FROM_NAME = "Monitoring Parquet Exporter"
MAX_EMAIL_ROWS = 40   # rincian di email dibatasi; sisanya cukup disebut jumlahnya (lengkapnya di Web UI)


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def from_address() -> str:
    """Alamat email di SMTP_FROM (tanpa nama tampilan 'Nama <alamat>')."""
    return parseaddr(SMTP_FROM)[1]


def from_name() -> str:
    """Nama tampilan pengirim: SMTP_FROM_NAME > nama di SMTP_FROM ('Nama <alamat>') > DEFAULT_FROM_NAME."""
    return SMTP_FROM_NAME or parseaddr(SMTP_FROM)[0].strip() or DEFAULT_FROM_NAME


def from_matches_login() -> bool:
    """
    Alamat pengirim == akun login SMTP? Kalau berbeda, mail server umumnya tetap MENERIMA email (250)
    lalu membuang/menolaknya di belakang (akun tidak berhak 'send as' alamat lain; penerima luar
    menolak karena SPF/DMARC) -- kegagalan senyap, makanya diperingatkan di halaman Pengaturan.
    """
    return not SMTP_USER or from_address().lower() == SMTP_USER.strip().lower()


def send_email(to: list[str], subject: str, text: str, html_body: str) -> str:
    """
    Kirim satu email (multipart teks + HTML). Error dilempar ke pemanggil. Return balasan server untuk
    DATA + Message-ID (mis. '250 2.6.0 <...> Queued mail for delivery') -- bahan untuk 'message trace'
    di mail server kalau email tidak sampai walau sudah diterima server.
    """
    sender = from_address()
    msg = EmailMessage()
    msg["From"] = formataddr((from_name(), sender))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    # Penanda email otomatis (RFC 3834) + cegah balasan otomatis "Out of Office" Exchange/Outlook ke akun pengirim.
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    raw = msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))
    if SMTP_SECURITY == "ssl":
        client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, local_hostname=SMTP_LOCAL_HOSTNAME,
                                  context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30, local_hostname=SMTP_LOCAL_HOSTNAME)
    with client:
        client.ehlo()
        if SMTP_SECURITY == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if SMTP_USER:
            client.login(SMTP_USER, SMTP_PASSWORD)
        # MAIL/RCPT/DATA manual (bukan send_message) supaya balasan server untuk DATA bisa dicatat.
        code, resp = client.mail(sender)
        if code != 250:
            raise smtplib.SMTPSenderRefused(code, resp, sender)
        refused = {}
        for rcpt in to:
            code, resp = client.rcpt(rcpt)
            if code not in (250, 251):
                refused[rcpt] = (code, resp)
        if len(refused) == len(to):
            raise smtplib.SMTPRecipientsRefused(refused)
        code, resp = client.data(raw)
        if code != 250:
            raise smtplib.SMTPDataError(code, resp)
    detail = f"{code} {resp.decode(errors='replace')} · Message-ID {msg['Message-ID']}"
    if refused:
        detail += f" · ditolak: {', '.join(refused)}"
    return detail[:500]


def _record(conn, kind, subject, recipients, status, error=None, run_id=None, job_names=None, detail=None) -> None:
    conn.execute("INSERT INTO notifications (kind, subject, recipients, status, error, run_id, job_names, detail, "
                 "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (kind, subject, json.dumps(recipients), status, error, run_id,
                  json.dumps(job_names) if job_names else None, detail, utc_now()))


def deliver(conn: sqlite3.Connection, kind: str, recipients: list[str], subject: str, text: str, html_body: str,
            run_id: int | None = None, job_names: list[str] | None = None) -> tuple[str, str | None]:
    """Kirim + catat. Return (status, error) -- status: sent | failed | skipped."""
    recipients = sorted({r.strip().lower() for r in recipients if r and EMAIL_RE.match(r.strip())})
    detail = None
    if not recipients:
        status, error = "skipped", "Tidak ada penerima."
    elif not smtp_configured():
        status, error = "skipped", "SMTP belum diatur di .env server (SMTP_HOST / SMTP_FROM)."
    else:
        try:
            detail = send_email(recipients, subject, text, html_body)
            status, error = "sent", None
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            status, error = "failed", f"{type(e).__name__}: {e}"[:500]
    _record(conn, kind, subject, recipients, status, error, run_id, job_names, detail)
    return status, error


# ---------------------------------------------------------------------------
# Isi email
# ---------------------------------------------------------------------------

def _link(path: str) -> str | None:
    return f"{APP_BASE_URL}{path}" if APP_BASE_URL else None


# (warna utama, latar lembut, teks tegas, label lencana) -- palet sama dengan Web UI.
_TONES = {
    "danger":  ("#dc2626", "#fef2f2", "#991b1b", "Perlu tindakan"),
    "warning": ("#d97706", "#fffbeb", "#92400e", "Perlu diperiksa"),
    "success": ("#059669", "#ecfdf5", "#065f46", "Info"),
    "neutral": ("#6b7280", "#f3f4f6", "#374151", ""),
}
_STATUS_LABEL = {"failed": "gagal", "interrupted": "terputus", "skipped": "dilewati", "cancelled": "dibatalkan",
                 "success": "sukses"}
_FONT = "'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _status_tone(value: str) -> str:
    """Warna lencana status per baris rincian (satu email bisa campuran gagal + peringatan DQ)."""
    v = value.lower()
    if v.startswith(("gagal", "terputus", "belum ada")):
        return "danger"
    if v.startswith(("dilewati", "dibatalkan")):
        return "neutral"
    return "warning"


def _sent_at() -> str:
    return datetime.now(timezone.utc).astimezone(WIB).strftime("%d-%m-%Y %H:%M WIB")


def _badge(label: str, tone: str) -> str:
    _, soft, strong, _ = _TONES[tone]
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;background:{soft};color:{strong};'
            f'font-size:12px;font-weight:600;line-height:20px;white-space:nowrap">{html.escape(label)}</span>')


def _card(row: tuple, headers: tuple[str, ...], badge_col: int | None, tone: str) -> str:
    """Satu baris rincian sebagai kartu bertumpuk -- tetap terbaca di layar HP (tabel 4 kolom terpotong)."""
    e = html.escape
    row_tone = _status_tone(str(row[badge_col])) if badge_col is not None else tone
    accent = _TONES[row_tone][0]
    head = (f'<div style="font-size:15px;font-weight:600;color:#111827;line-height:1.4;word-break:break-all">'
            f'{e(str(row[0]))}</div>')
    if badge_col is not None:
        head += f'<div style="margin-top:6px">{_badge(str(row[badge_col]), row_tone)}</div>'
    fields = ""
    for i, (label, value) in enumerate(zip(headers, row)):
        if i == 0 or i == badge_col or value in (None, ""):
            continue
        fields += (f'<div style="margin-top:10px"><div style="font-size:11px;font-weight:600;color:#6b7280;'
                   f'text-transform:uppercase;letter-spacing:.04em">{e(label)}</div>'
                   f'<div style="font-size:14px;color:#1f2937;line-height:1.5;word-break:break-word">{e(str(value))}'
                   f'</div></div>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:12px;border:1px solid #e5e7eb;border-left:4px solid {accent};border-radius:8px">'
            f'<tr><td style="padding:14px 16px">{head}{fields}</td></tr></table>')


def _html(subject: str, title: str, intro: str, rows: list[tuple], headers: tuple[str, ...], link: str | None,
          link_label: str, tone: str, facts: list[tuple[str, str]], badge_col: int | None,
          alert: tuple[str, str] | None) -> str:
    """
    Email HTML responsif: kerangka tabel + inline style (Outlook desktop memakai mesin Word, Gmail membuang
    CSS eksternal), lebar maks 640px yang menyusut di HP, rincian sebagai kartu bertumpuk -- bukan tabel lebar.
    Satu-satunya <style> hanya untuk merapatkan padding di layar kecil; tanpa itu tampilan tetap utuh.
    """
    e = html.escape
    accent, soft, strong, kicker = _TONES[tone]
    preheader = intro if len(intro) <= 140 else intro[:137] + "..."
    facts_html = ""
    if facts:
        facts_html = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                      'style="margin-top:20px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px">'
                      + "".join(f'<tr><td class="fact-k" style="padding:8px 14px;font-size:13px;color:#6b7280;'
                                f'width:40%;vertical-align:top{";border-top:1px solid #e5e7eb" if n else ""}">{e(k)}</td>'
                                f'<td style="padding:8px 14px;font-size:13px;color:#111827;font-weight:600;'
                                f'vertical-align:top;word-break:break-word{";border-top:1px solid #e5e7eb" if n else ""}">'
                                f'{e(str(v))}</td></tr>' for n, (k, v) in enumerate(facts))
                      + '</table>')
    alert_html = ""
    if alert:   # (tone, teks): kotak sorotan di bawah intro -- error run (danger) / catatan (neutral)
        a_accent, a_soft, a_strong, _ = _TONES[alert[0]]
        alert_html = (f'<div style="margin-top:16px;padding:12px 14px;background:{a_soft};border-left:4px solid '
                      f'{a_accent};border-radius:6px;font-size:13px;color:{a_strong};line-height:1.5;'
                      f'word-break:break-word">{e(alert[1])}</div>')
    details = ""
    if rows:
        shown = rows[:MAX_EMAIL_ROWS]
        details = (f'<div style="margin-top:28px;font-size:13px;font-weight:700;color:#374151;'
                   f'text-transform:uppercase;letter-spacing:.05em">Rincian ({len(rows)})</div>'
                   + "".join(_card(r, headers, badge_col, tone) for r in shown))
        if len(rows) > len(shown):
            details += (f'<p style="margin:12px 0 0;font-size:13px;color:#6b7280">... dan {len(rows) - len(shown)} '
                        f'lainnya. Daftar lengkap ada di Web UI.</p>')
    button = ""
    if link:
        # Tombol "bulletproof": warna di sel tabel (bgcolor) supaya tetap tampil di Outlook desktop.
        button = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                  f'style="margin-top:28px"><tr><td bgcolor="{accent}" style="border-radius:8px;background:{accent}">'
                  f'<a href="{e(link)}" style="display:inline-block;padding:12px 22px;font-family:{_FONT};font-size:15px;'
                  f'font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px">{e(link_label)} &rarr;</a>'
                  f'</td></tr></table>'
                  f'<p style="margin:12px 0 0;font-size:12px;color:#9ca3af;line-height:1.5;word-break:break-all">'
                  f'Tombol tidak berfungsi? Salin tautan ini: <a href="{e(link)}" style="color:#6b7280">{e(link)}</a></p>')
    settings_link = _link("/settings")
    footer_link = (f' Ubah penerima di <a href="{e(settings_link)}" style="color:#6b7280">halaman Pengaturan</a>.'
                   if settings_link else "")
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting"><meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light"><title>{e(subject)}</title>
<style>
@media only screen and (max-width:620px) {{
  .outer {{ padding:10px 6px !important; }}
  .px {{ padding-left:16px !important; padding-right:16px !important; }}
  .h1 {{ font-size:20px !important; }}
  .fact-k {{ width:45% !important; }}
}}
</style></head>
<body style="margin:0;padding:0;background:#f3f4f6;-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f3f4f6">
<tr><td class="outer" align="center" style="padding:24px 12px">
<!--[if mso]><table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="max-width:640px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;font-family:{_FONT}">
<tr><td class="px" bgcolor="#064e3b" style="background:#064e3b;padding:16px 28px;border-radius:12px 12px 0 0">
  <div style="font-size:16px;font-weight:700;color:#ffffff">{e(APP_NAME)}</div>
  <div style="font-size:12px;color:#a7f3d0;margin-top:2px">Sistem Monitoring &middot; Pengingat otomatis</div>
</td></tr>
<tr><td style="height:4px;line-height:4px;font-size:0;background:{accent}" bgcolor="{accent}">&nbsp;</td></tr>
<tr><td class="px" style="padding:24px 28px 28px">
  {_badge(kicker, tone) if kicker else ""}
  <h1 class="h1" style="margin:12px 0 0;font-size:22px;line-height:1.3;color:#111827;font-weight:700">{e(title)}</h1>
  <p style="margin:10px 0 0;font-size:15px;line-height:1.6;color:#4b5563">{e(intro)}</p>
  {alert_html}{facts_html}{details}{button}
</td></tr>
<tr><td class="px" style="padding:18px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;border-radius:0 0 12px 12px">
  <p style="margin:0;font-size:12px;line-height:1.6;color:#6b7280">
  Email ini dikirim otomatis oleh <strong>Sistem Monitoring {e(APP_NAME)}</strong> pada {_sent_at()} &mdash;
  tidak perlu dibalas. Anda menerimanya karena terdaftar sebagai penerima notifikasi.{footer_link}</p>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body></html>"""


def _text(title: str, intro: str, rows: list[tuple], headers: tuple[str, ...], link: str | None,
          facts: list[tuple[str, str]], badge_col: int | None, alert: tuple[str, str] | None) -> str:
    """Versi teks polos (klien yang menolak HTML / pratinjau notifikasi)."""
    lines = [f"{APP_NAME} - Sistem Monitoring", "", title, "=" * len(title), "", intro]
    if alert:
        lines += ["", alert[1]]
    if facts:
        lines += [""] + [f"{k}: {v}" for k, v in facts]
    if rows:
        lines += ["", f"RINCIAN ({len(rows)})"]
        for r in rows[:MAX_EMAIL_ROWS]:
            lines.append(f"- {r[0]}" + (f" [{r[badge_col]}]" if badge_col is not None else ""))
            lines += [f"    {h}: {v}" for i, (h, v) in enumerate(zip(headers, r))
                      if i not in (0, badge_col) and v not in (None, "")]
        if len(rows) > MAX_EMAIL_ROWS:
            lines.append(f"... dan {len(rows) - MAX_EMAIL_ROWS} lainnya. Daftar lengkap ada di Web UI.")
    if link:
        lines += ["", f"Buka: {link}"]
    lines += ["", "--", f"Email otomatis dari Sistem Monitoring {APP_NAME} ({_sent_at()}), tidak perlu dibalas."]
    return "\n".join(lines)


def _send(conn: sqlite3.Connection, kind: str, recipients: list[str], subject: str, title: str, intro: str, *,
          rows: list[tuple] = (), headers: tuple[str, ...] = (), link: str | None = None, link_label: str = "",
          tone: str = "success", facts: list[tuple[str, str]] = (), badge_col: int | None = None,
          alert: tuple[str, str] | None = None, run_id: int | None = None,
          job_names: list[str] | None = None) -> tuple[str, str | None]:
    """Susun versi teks + HTML dari isi yang sama, lalu deliver()."""
    rows, facts = list(rows), list(facts)
    return deliver(conn, kind, recipients, subject,
                   _text(title, intro, rows, headers, link, facts, badge_col, alert),
                   _html(subject, title, intro, rows, headers, link, link_label, tone, facts, badge_col, alert),
                   run_id=run_id, job_names=job_names)


def _run_dates(run: dict) -> str:
    return run["start_date"] if run["start_date"] == run["end_date"] else f"{run['start_date']} s/d {run['end_date']}"


def _run_by(run: dict) -> str:
    return run["created_by"].replace("jadwal:", "jadwal ")


def _recipients(conn: sqlite3.Connection, run: dict, settings: dict) -> list[str]:
    to = list(settings["notify.recipients"])
    creator = run["created_by"]
    if settings["notify.notify_creator"] and not creator.startswith("jadwal:"):
        r = conn.execute("SELECT email FROM users WHERE username = ? AND active = 1", (creator,)).fetchone()
        if r and r["email"]:
            to.append(r["email"])
    return to


def dq_warning_rows(conn: sqlite3.Connection, run: dict) -> list[tuple[str, str, str, str]]:
    """Peringatan data quality run ini: per item (dq_detail) + volume per periode (job mingguan/bulanan)."""
    rows, executed = [], {}
    for i in run["items"]:
        if i["status"] != "success":
            continue
        warns = [c["message"] for c in (i.get("dq_detail") or []) if c["status"] == dq.WARNING]
        if warns:
            rows.append((i["job_name"], i["target_date"], "peringatan DQ", "; ".join(warns)))
        cadence = dq.cadence_of(conn, i["job_name"], i["template"], i["args"])
        executed.setdefault(i["job_name"], (cadence, set()))[1].add(i["target_date"])
    rows += [(f["job_name"], f["period"], "peringatan DQ", f"volume periode: {f['message']}")
             for f in dq.run_period_findings(conn, executed)]
    return rows


def notify_dq_warnings(conn: sqlite3.Connection, run: dict, settings: dict, rows: list) -> None:
    subject = f"{SUBJECT_PREFIX} Run #{run['id']}: {len(rows)} peringatan data quality"
    intro = (f"Run #{run['id']} sudah selesai dan data tetap terupload, tetapi ada {len(rows)} hasil yang tidak "
             f"biasa. Mohon periksa apakah memang wajar.")
    facts = [("Run", f"#{run['id']} ({_STATUS_LABEL.get(run['status'], run['status'])})"),
             ("Tanggal data", _run_dates(run)), ("Dijalankan oleh", _run_by(run)),
             ("Eksekusi sukses", f"{run['ok_items']} dari {run['total_items']}"), ("Peringatan", str(len(rows)))]
    _send(conn, "dq_warning", _recipients(conn, run, settings), subject, "Ada peringatan data quality", intro,
          rows=rows, headers=("Job", "Tanggal / periode", "Status", "Keterangan"), badge_col=2,
          link=_link(f"/runs/{run['id']}"), link_label=f"Lihat Run #{run['id']}", tone="warning", facts=facts,
          run_id=run["id"])


def notify_run_failed(conn: sqlite3.Connection, run: dict, settings: dict, dq_rows: list | None = None) -> None:
    bad = [i for i in run["items"] if i["status"] in ("failed", "interrupted", "skipped", "cancelled")]
    failed = [i for i in bad if i["status"] in ("failed", "interrupted")]
    status_label = "TERPUTUS" if run["status"] == "interrupted" else "GAGAL"
    subject = (f"{SUBJECT_PREFIX} Run #{run['id']} {status_label} — {len(failed)} dari {run['total_items']} "
               f"eksekusi gagal")
    intro = (f"Run #{run['id']} berhenti dengan status {status_label.lower()}: {len(failed)} eksekusi gagal, "
             f"jadi data untuk job tersebut belum masuk S3. Periksa pesan error di bawah, perbaiki penyebabnya, "
             f"lalu jalankan ulang.")
    facts = [("Run", f"#{run['id']} ({status_label.lower()})"), ("Tanggal data", _run_dates(run)),
             ("Dijalankan oleh", _run_by(run)),
             ("Hasil", f"{run['ok_items']} sukses, {len(failed)} gagal dari {run['total_items']} eksekusi")]
    rows = ([(i["job_name"], i["target_date"], _STATUS_LABEL.get(i["status"], i["status"]), (i["error"] or "")[:300])
             for i in bad] + (dq_rows or []))
    _send(conn, "run_failed", _recipients(conn, run, settings), subject, f"Run #{run['id']} {status_label.lower()}",
          intro, rows=rows, headers=("Job", "Tanggal", "Status", "Pesan"), badge_col=2,
          link=_link(f"/runs/{run['id']}"), link_label=f"Lihat Run #{run['id']}", tone="danger", facts=facts,
          alert=("danger", f"Error run: {run['error']}") if run.get("error") else None, run_id=run["id"])


def detect_anomalies(conn: sqlite3.Connection, run: dict, streak: int) -> list[dict]:
    """
    Job berirama HARIAN di run ini yang N tanggal terakhirnya semua 0 baris, padahal sebelumnya pernah
    berisi data, dan belum pernah diberi tahu sejak terakhir kali berisi data. Job mingguan/bulanan tidak
    ikut -- di hari biasa memang wajar 0 baris (volumenya dinilai per periode, lihat dq.py).
    """
    found = []
    candidates = {i["job_name"] for i in run["items"] if i["status"] == "success" and i["rows"] == 0
                  and dq.cadence_of(conn, i["job_name"], i["template"], i["args"]) == "daily"}
    for name in candidates:
        latest = dq_latest_rows(conn, name)[:streak]
        if len(latest) < streak or any(r["rows"] for r in latest):
            continue
        last_data = conn.execute("SELECT MAX(created_at) FROM dq_results WHERE job_name = ? AND rows > 0 "
                                 "AND dq_status != 'blocked'", (name,)).fetchone()[0]
        if not last_data:
            continue  # belum pernah berisi data sama sekali -- bukan anomali (mungkin memang tabel kosong)
        # instr, BUKAN LIKE: '_' di LIKE = wildcard satu karakter, dan nama job penuh underscore.
        # >= (bukan >): timestamp presisi detik -- notifikasi bisa tercatat di detik yang SAMA dengan
        # item terakhir yang berisi data (run range yang cepat), dan selalu sesudahnya.
        already = conn.execute("SELECT 1 FROM notifications WHERE kind = 'anomaly' AND status = 'sent' "
                               "AND created_at >= ? AND instr(job_names, ?) > 0", (last_data, f'"{name}"')).fetchone()
        if not already:
            last_date = conn.execute("SELECT MAX(target_date) FROM dq_results WHERE job_name = ? AND rows > 0 "
                                     "AND dq_status != 'blocked'", (name,)).fetchone()[0]
            found.append({"job_name": name, "dates": [r["target_date"] for r in latest], "last_data_date": last_date})
    return found


def notify_anomalies(conn: sqlite3.Connection, run: dict, settings: dict) -> None:
    streak = int(settings["notify.anomaly_streak"])
    anomalies = detect_anomalies(conn, run, streak)
    if not anomalies:
        return
    names = [a["job_name"] for a in anomalies]
    subject = f"{SUBJECT_PREFIX} Anomali: {len(names)} job 0 baris {streak} tanggal berturut-turut"
    intro = (f"Job berikut biasanya selalu berisi data, tetapi {streak} tanggal terakhir menghasilkan 0 baris. "
             f"Kemungkinan filter tanggal tidak cocok lagi, atau data di sumber berhenti masuk.")
    facts = [("Job terdampak", str(len(names))), ("Kriteria", f"0 baris {streak} tanggal berturut-turut"),
             ("Terdeteksi di", f"Run #{run['id']}")]
    rows = [(a["job_name"], ", ".join(sorted(a["dates"])), a["last_data_date"]) for a in anomalies]
    _send(conn, "anomaly", _recipients(conn, run, settings), subject, "Job tiba-tiba kosong", intro,
          rows=rows, headers=("Job", "Tanggal 0 baris", "Terakhir berisi data"), link=_link("/jobs"),
          link_label="Buka Daftar Job", tone="warning", facts=facts, run_id=run["id"], job_names=names)


def notify_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Dipanggil worker setelah run selesai. Tidak pernah melempar error."""
    run = run_store.get_run(conn, run_id)
    if not run:
        return
    settings = get_settings(conn, "notify.")
    dq_rows = dq_warning_rows(conn, run) if settings["notify.on_dq_warning"] else []
    if settings["notify.on_failed"] and run["status"] in ("failed", "interrupted"):
        notify_run_failed(conn, run, settings, dq_rows)
    elif dq_rows:
        notify_dq_warnings(conn, run, settings, dq_rows)
    if settings["notify.on_anomaly"]:
        notify_anomalies(conn, run, settings)


def notify_stale(conn: sqlite3.Connection, result: dict) -> None:
    """Email penjaga kesegaran: job aktif yang belum memproses tanggal terbaru yang diharapkan."""
    late, gaps = result["late"], result.get("gaps", [])
    subject = (f"{SUBJECT_PREFIX} Kesegaran {result['date']}: {len(late)} job belum segar, "
               f"{len(gaps)} job ada celah")
    intro = ("Pengingat harian: ada job yang datanya belum terbaru atau masih bolong. Cek jadwal, worker, dan "
             "kelengkapan sumber data, lalu jalankan ulang tanggal yang kurang dari halaman Jalankan.")
    facts = [("Tanggal pemeriksaan", result["date"]), ("Job aktif diperiksa", str(result["checked"])),
             ("Belum segar", f"{len(late)} job"), ("Ada celah", f"{len(gaps)} job")]
    alert = None
    if gaps:
        alert = ("neutral", "Celah = tanggal/minggu/bulan di tengah riwayat yang tidak ada di S3 maupun catatan "
                            "pipeline. Bisa juga memang 0 baris -- jalankan ulang rentang itu untuk memastikan.")
    rows = [(f["job_name"], "belum ada data" if f["status"] == "unknown" else "terlambat", f["cadence"],
             f"terbaru {f['latest'] or '-'}, seharusnya {f['expected']}") for f in late]
    rows += [(g["job_name"], f"celah ({g['count']})", g["cadence"], "; ".join(g["items"][:6])) for g in gaps]
    to = get_settings(conn, "notify.")["notify.recipients"]
    _send(conn, "stale", to, subject, "Data terlambat / ada celah", intro, rows=rows,
          headers=("Job", "Masalah", "Irama", "Keterangan"), badge_col=1, link=_link("/jobs"),
          link_label="Buka Daftar Job", tone="danger" if late else "warning", facts=facts, alert=alert,
          job_names=sorted({f["job_name"] for f in late} | {g["job_name"] for g in gaps}))


def send_test(conn: sqlite3.Connection, to: str, by: str) -> tuple[str, str | None]:
    subject = f"{SUBJECT_PREFIX} Email uji"
    intro = "Kalau Anda membaca email ini, setelan SMTP sudah benar dan notifikasi monitoring siap dikirim."
    facts = [("Dikirim oleh", by), ("Nama pengirim", from_name())]
    return _send(conn, "test", [to], subject, "Email uji berhasil", intro, link=_link("/"),
                 link_label=f"Buka {APP_NAME}", tone="success", facts=facts)
