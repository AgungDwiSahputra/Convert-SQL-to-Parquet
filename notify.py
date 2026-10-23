"""
NOTIFIKASI EMAIL -- dikirim worker.py setelah run selesai.

  run_failed : run gagal / terputus -> penerima tetap (+ pembuat run kalau setelan aktif)
  anomaly    : job yang PERNAH berisi data, tapi 0 baris pada N tanggal terakhir berturut-turut
               (kasus kegagalan senyap, mis. filter tanggal salah format). Dikirim SEKALI per
               kejadian: tidak dikirim ulang sampai job itu kembali berisi data lalu kosong lagi.
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
from email.utils import formatdate, make_msgid, parseaddr

import run_store
from app_settings import get_settings
from config import (
    APP_BASE_URL, SMTP_FROM, SMTP_HOST, SMTP_LOCAL_HOSTNAME, SMTP_PASSWORD, SMTP_PORT, SMTP_SECURITY, SMTP_USER,
)
from job_store import utc_now

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SUBJECT_PREFIX = "[Parquet Exporter]"
_DAILY_TEMPLATES = ("bronze_daily", "bronze_daily_strdate", "bronze_daily_strdate_eq", "bronze_daily_epoch_ms",
                    "bronze_daily_join")


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def from_address() -> str:
    """Alamat email di SMTP_FROM (tanpa nama tampilan 'Nama <alamat>')."""
    return parseaddr(SMTP_FROM)[1]


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
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
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


def _html(title: str, intro: str, rows: list[tuple[str, ...]], headers: tuple[str, ...], link: str | None,
          link_label: str, accent: str = "#059669") -> str:
    """Email HTML sederhana (inline style -- klien email tidak membaca CSS eksternal)."""
    e = html.escape
    table = ""
    if rows:
        head = "".join(f'<th style="text-align:left;padding:8px 10px;font-size:12px;color:#6b7280;'
                       f'border-bottom:1px solid #e5e7eb">{e(h)}</th>' for h in headers)
        # Semua kolom kecuali yang terakhir (keterangan/error panjang) tidak dibungkus -- tanggal tetap satu baris.
        body = "".join("<tr>" + "".join(f'<td style="padding:8px 10px;font-size:13px;color:#1f2937;'
                                        f'border-bottom:1px solid #f3f4f6;vertical-align:top'
                                        f'{";white-space:nowrap" if i < len(r) - 1 else ""}">{e(str(c))}</td>'
                                        for i, c in enumerate(r)) + "</tr>" for r in rows)
        table = f'<table style="width:100%;border-collapse:collapse;margin-top:16px">{head}{body}</table>'
    button = (f'<p style="margin-top:24px"><a href="{e(link)}" style="background:{accent};color:#ffffff;'
              f'text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:600;font-size:14px">'
              f'{e(link_label)}</a></p>') if link else ""
    return (f'<div style="background:#f3f4f6;padding:24px;font-family:Segoe UI,Arial,sans-serif">'
            f'<div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;'
            f'border:1px solid #e5e7eb">'
            f'<div style="background:#064e3b;color:#ffffff;padding:16px 24px;font-weight:600">Parquet Exporter</div>'
            f'<div style="padding:24px"><h2 style="margin:0;font-size:18px;color:#1f2937">{e(title)}</h2>'
            f'<p style="margin:8px 0 0;font-size:14px;color:#6b7280;line-height:1.5">{e(intro)}</p>'
            f'{table}{button}</div></div></div>')


def _text(title: str, intro: str, rows: list[tuple[str, ...]], link: str | None) -> str:
    lines = [title, "", intro, ""]
    lines += [" | ".join(str(c) for c in r) for r in rows]
    if link:
        lines += ["", f"Buka: {link}"]
    return "\n".join(lines)


def _recipients(conn: sqlite3.Connection, run: dict, settings: dict) -> list[str]:
    to = list(settings["notify.recipients"])
    creator = run["created_by"]
    if settings["notify.notify_creator"] and not creator.startswith("jadwal:"):
        r = conn.execute("SELECT email FROM users WHERE username = ? AND active = 1", (creator,)).fetchone()
        if r and r["email"]:
            to.append(r["email"])
    return to


def notify_run_failed(conn: sqlite3.Connection, run: dict, settings: dict) -> None:
    bad = [i for i in run["items"] if i["status"] in ("failed", "interrupted", "skipped", "cancelled")]
    failed = [i for i in bad if i["status"] in ("failed", "interrupted")]
    status_label = "TERPUTUS" if run["status"] == "interrupted" else "GAGAL"
    subject = (f"{SUBJECT_PREFIX} Run #{run['id']} {status_label} — {len(failed)} dari {run['total_items']} "
               f"eksekusi gagal")
    who = run["created_by"].replace("jadwal:", "jadwal ")
    intro = (f"Run #{run['id']} (oleh {who}, tanggal data {run['start_date']}"
             f"{'' if run['start_date'] == run['end_date'] else ' s/d ' + run['end_date']}) selesai dengan status "
             f"{status_label}. {run['ok_items']} eksekusi sukses."
             + (f" Error run: {run['error']}" if run.get("error") else ""))
    rows = [(i["job_name"], i["target_date"], i["status"], (i["error"] or "")[:300]) for i in bad[:30]]
    link = _link(f"/runs/{run['id']}")
    deliver(conn, "run_failed", _recipients(conn, run, settings), subject,
            _text(subject, intro, rows, link),
            _html(f"Run #{run['id']} {status_label.lower()}", intro, rows, ("Job", "Tanggal", "Status", "Error"),
                  link, f"Buka Run #{run['id']}", accent="#dc2626"),
            run_id=run["id"])


def _is_daily(template: str, args: dict) -> bool:
    return template in _DAILY_TEMPLATES or (template in ("sp_job", "custom_sql") and args.get("needs_date", True))


def detect_anomalies(conn: sqlite3.Connection, run: dict, streak: int) -> list[dict]:
    """
    Job harian di run ini yang N tanggal terakhirnya (sukses) semua 0 baris, padahal sebelumnya
    pernah berisi data, dan belum pernah diberi tahu sejak terakhir kali berisi data.
    """
    found = []
    candidates = {i["job_name"]: i for i in run["items"] if i["status"] == "success" and i["rows"] == 0
                  and _is_daily(i["template"], i["args"])}
    for name in candidates:
        latest = conn.execute(
            "SELECT target_date, MAX(rows) AS rows, MAX(finished_at) AS finished_at FROM run_items "
            "WHERE job_name = ? AND status = 'success' GROUP BY target_date ORDER BY target_date DESC LIMIT ?",
            (name, streak)).fetchall()
        if len(latest) < streak or any(r["rows"] for r in latest):
            continue
        last_data = conn.execute("SELECT MAX(finished_at) FROM run_items WHERE job_name = ? AND status = 'success' "
                                 "AND rows > 0", (name,)).fetchone()[0]
        if not last_data:
            continue  # belum pernah berisi data sama sekali -- bukan anomali (mungkin memang tabel kosong)
        # instr, BUKAN LIKE: '_' di LIKE = wildcard satu karakter, dan nama job penuh underscore.
        # >= (bukan >): timestamp presisi detik -- notifikasi bisa tercatat di detik yang SAMA dengan
        # item terakhir yang berisi data (run range yang cepat), dan selalu sesudahnya.
        already = conn.execute("SELECT 1 FROM notifications WHERE kind = 'anomaly' AND status = 'sent' "
                               "AND created_at >= ? AND instr(job_names, ?) > 0", (last_data, f'"{name}"')).fetchone()
        if not already:
            last_date = conn.execute("SELECT MAX(target_date) FROM run_items WHERE job_name = ? AND status = 'success' "
                                     "AND rows > 0", (name,)).fetchone()[0]
            found.append({"job_name": name, "dates": [r["target_date"] for r in latest], "last_data_date": last_date})
    return found


def notify_anomalies(conn: sqlite3.Connection, run: dict, settings: dict) -> None:
    streak = int(settings["notify.anomaly_streak"])
    anomalies = detect_anomalies(conn, run, streak)
    if not anomalies:
        return
    names = [a["job_name"] for a in anomalies]
    subject = f"{SUBJECT_PREFIX} Anomali: {len(names)} job 0 baris {streak} tanggal berturut-turut"
    intro = (f"Job berikut sebelumnya selalu berisi data, tapi {streak} tanggal terakhir menghasilkan 0 baris. "
             f"Kemungkinan filter tanggal tidak cocok lagi, atau data di sumber berhenti masuk.")
    rows = [(a["job_name"], ", ".join(sorted(a["dates"])), a["last_data_date"]) for a in anomalies]
    link = _link("/jobs")
    deliver(conn, "anomaly", _recipients(conn, run, settings), subject, _text(subject, intro, rows, link),
            _html("Anomali: job tiba-tiba kosong", intro, rows, ("Job", "Tanggal 0 baris", "Tanggal terakhir berisi data"),
                  link, "Buka Daftar Job", accent="#d97706"),
            run_id=run["id"], job_names=names)


def notify_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Dipanggil worker setelah run selesai. Tidak pernah melempar error."""
    run = run_store.get_run(conn, run_id)
    if not run:
        return
    settings = get_settings(conn, "notify.")
    if settings["notify.on_failed"] and run["status"] in ("failed", "interrupted"):
        notify_run_failed(conn, run, settings)
    if settings["notify.on_anomaly"]:
        notify_anomalies(conn, run, settings)


def send_test(conn: sqlite3.Connection, to: str, by: str) -> tuple[str, str | None]:
    subject = f"{SUBJECT_PREFIX} Email uji"
    intro = f"Email ini dikirim dari halaman Pengaturan oleh {by}. Kalau Anda menerimanya, setelan SMTP sudah benar."
    return deliver(conn, "test", [to], subject, _text(subject, intro, [], _link("/")),
                   _html("Email uji berhasil", intro, [], (), _link("/"), "Buka Parquet Exporter"))
