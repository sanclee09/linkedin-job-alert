#!/usr/bin/env python3
"""
LinkedIn Job Alert - Daily email with top 5 AI/ML jobs in Munich
Uses python-jobspy (no auth required) + Gmail SMTP
"""

import email as email_lib
import imaplib
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from jobspy import scrape_jobs

load_dotenv()

# --- CONFIG ------------------------------------------------------------------
RECIPIENT_EMAIL    = "sang.h.lee09@gmail.com"
SENDER_EMAIL       = "sang.h.lee09@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

SEARCH_QUERIES = [
    "AI Engineer LLM RAG",
    "Data Scientist NLP Python",
    "ML Engineer Deep Learning",
    "Data Engineer Python SQL ETL",
    "AI Consultant GenAI",
    "Quant Data Scientist",
]
LOCATION   = "Munich, Germany"
HOURS_OLD  = 168   # past week
TOP_N      = 5

SEEN_JOBS_FILE = Path(os.environ.get("SEEN_JOBS_FILE", Path.home() / ".linkedin_job_alert_seen.json"))

APPLIED_JOBS_FILE = Path(os.environ.get("APPLIED_JOBS_FILE", Path.home() / ".linkedin_job_alert_applied.json"))

# Exclude senior+ level positions
EXCLUDE_TITLE_KEYWORDS = [
    "senior", "staff", "principal", "lead", "head of", "director", "vp ",
    "vice president", "manager", "architect", "working student", "werkstudent",
]
# -----------------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    """Strip gender tags, location suffixes, and whitespace for comparison."""
    t = title.strip().lower()
    # Remove gender markers like (m/w/d), (f/m/d), (all genders), (w/m/d), (m/f/d) etc.
    t = re.sub(r"\s*\([mfwdx/]+\)", "", t)
    t = re.sub(r"\s*\(all genders?\)", "", t)
    # Remove location / office suffixes after " - " or " – "
    t = re.split(r"\s+[-–]\s+", t)[0]
    # Remove trailing whitespace and common noise
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _job_key(title: str, company: str) -> str:
    """Normalize title+company into a comparable key."""
    return f"{title.strip().lower()}@{company.strip().lower()}"


def load_applied_jobs() -> set:
    """Load set of title+company keys for jobs already applied to."""
    if APPLIED_JOBS_FILE.exists():
        with open(APPLIED_JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_applied_jobs(applied: set):
    APPLIED_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(APPLIED_JOBS_FILE, "w") as f:
        json.dump(list(applied), f)


def _decode_header(raw_header: str) -> str:
    """Decode an email header value to a plain string."""
    parts = email_lib.header.decode_header(raw_header)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def fetch_applied_jobs_from_gmail() -> set[str]:
    """Fetch applied job titles via Gmail IMAP (LinkedIn + direct applications)."""
    if not GMAIL_APP_PASSWORD:
        return set()
    applied_titles: set[str] = set()
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        mail.select('"[Gmail]/All Mail"')

        # --- 1) LinkedIn application confirmations ---
        _, data = mail.search(None, 'FROM "jobs-noreply@linkedin.com" SUBJECT "application was sent"')
        for msg_id in data[0].split():
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    for link_match in re.finditer(
                        r'<a[^>]*href="[^"]*jobs/view[^"]*"[^>]*>(.*?)</a>', html, re.S,
                    ):
                        text = re.sub(r"<[^>]+>", "", link_match.group(1)).strip()
                        text = text.replace("&amp;", "&")
                        if text:
                            applied_titles.add(_normalize_title(text))
                            break
                    break

        # --- 2) Direct application confirmations (Bewerbung / application) ---
        _, data2 = mail.search(
            None, 'OR SUBJECT "Deine Bewerbung als" SUBJECT "Your application for"',
        )
        # Common patterns:
        #   "Deine Bewerbung als AI Engineer (m/w/d) - München oder Mobile Office"
        #   "Your application for the position of AI Technology Expert (m/f/d)"
        #   "INVENSITY - Deine Bewerbung als Data Science Consultant (m/w/d)"
        patterns = [
            r"Bewerbung als (.+)",
            r"[Yy]our application (?:for |as )(?:the position of )?(.+)",
            r"Bewerbung auf die (?:Stelle|Position) (.+)",
            r"[Cc]onfirmation of your application.*?[-–]\s*(.+)",
        ]
        for msg_id in data2[0].split():
            _, msg_data = mail.fetch(msg_id, "(BODY[HEADER.FIELDS (SUBJECT FROM)])")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            sender = str(msg.get("From", ""))
            if SENDER_EMAIL in sender:
                continue  # skip own replies
            subject = _decode_header(msg["Subject"]) if msg["Subject"] else ""
            for pattern in patterns:
                m = re.search(pattern, subject)
                if m:
                    title = m.group(1).strip()
                    # Clean trailing company info after common separators
                    title = re.split(r"\s+bei\s+|\s+at\s+|\s+[-–|]\s+", title)[0].strip()
                    if title:
                        applied_titles.add(_normalize_title(title))
                    break

        mail.logout()
        print(f"  Gmail IMAP: found {len(applied_titles)} applied job titles")
    except Exception as e:
        print(f"  [WARN] Gmail IMAP failed: {e}", file=sys.stderr)
    return applied_titles


def load_seen_jobs() -> set:
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_jobs(seen: set):
    SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f)


def detect_work_type(row: pd.Series) -> str:
    if row.get("is_remote"):
        return "Remote"
    loc = str(row.get("location", "")).lower()
    desc = str(row.get("description", "")).lower()[:500]
    if "hybrid" in loc or "hybrid" in desc:
        return "Hybrid"
    if "remote" in loc or "remote" in desc:
        return "Remote"
    return "On-site"


def search_jobs() -> list[dict]:
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    for query in SEARCH_QUERIES:
        print(f"  Searching: {query!r} in {LOCATION}")
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=query,
                location=LOCATION,
                results_wanted=15,
                hours_old=HOURS_OLD,
                linkedin_fetch_description=True,
            )
            for _, row in df.iterrows():
                job_id = str(row.get("id", "")).strip()
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = str(row.get("title", "Unknown Title"))
                if any(kw in title.lower() for kw in EXCLUDE_TITLE_KEYWORDS):
                    continue

                desc = str(row.get("description", ""))
                all_jobs.append({
                    "job_id":      job_id,
                    "title":       title,
                    "company":     str(row.get("company", "Unknown Company")),
                    "location":    str(row.get("location", LOCATION)),
                    "work_type":   detect_work_type(row),
                    "description": desc[:300].strip(),
                    "url":         str(row.get("job_url", f"https://www.linkedin.com/jobs/view/{job_id}/")),
                    "date_posted": row.get("date_posted"),
                })
        except Exception as e:
            print(f"  [WARN] Search failed for {query!r}: {e}", file=sys.stderr)

    # Filter out already-applied jobs (via Gmail IMAP, title match)
    applied_titles = fetch_applied_jobs_from_gmail()
    before = len(all_jobs)
    all_jobs = [
        j for j in all_jobs
        if _normalize_title(j["title"]) not in applied_titles
    ]
    if before != len(all_jobs):
        print(f"  Filtered {before - len(all_jobs)} already-applied jobs")

    # Sort newest first
    all_jobs.sort(
        key=lambda j: j["date_posted"] if isinstance(j["date_posted"], datetime) else datetime.min,
        reverse=True,
    )
    return all_jobs


def build_email_html(jobs: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    work_type_colors = {
        "Remote":  ("background:#d1fae5;color:#065f46;", "Remote"),
        "Hybrid":  ("background:#e8f4fd;color:#1a73e8;", "Hybrid"),
        "On-site": ("background:#fef3c7;color:#92400e;", "On-site"),
    }

    cards = ""
    for i, job in enumerate(jobs, 1):
        wt = job.get("work_type", "")
        badge_style, badge_label = work_type_colors.get(wt, ("", ""))
        work_badge = (
            f'<span style="{badge_style}padding:2px 8px;border-radius:10px;'
            f'font-size:12px;font-weight:600;">{badge_label}</span>'
        ) if badge_style else ""

        desc = job["description"]
        if len(desc) > 220:
            desc = desc[:220] + "…"

        # Escape basic HTML chars in user-sourced strings
        title   = job["title"].replace("&", "&amp;").replace("<", "&lt;")
        company = job["company"].replace("&", "&amp;").replace("<", "&lt;")
        location = job["location"].replace("&", "&amp;").replace("<", "&lt;")
        desc    = desc.replace("&", "&amp;").replace("<", "&lt;")

        cards += f"""
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;
                    padding:20px 24px;margin-bottom:16px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="background:#0a66c2;color:#fff;border-radius:50%;
                         width:24px;height:24px;display:inline-flex;
                         align-items:center;justify-content:center;
                         font-size:12px;font-weight:700;flex-shrink:0;">{i}</span>
            <span style="font-size:17px;font-weight:700;color:#111827;">{title}</span>
          </div>
          <div style="color:#374151;font-size:14px;margin:6px 0 4px 32px;">
            🏢 {company} &nbsp;·&nbsp; 📍 {location} &nbsp;{work_badge}
          </div>
          <div style="color:#6b7280;font-size:13px;margin:8px 0 12px 32px;line-height:1.5;">
            {desc}
          </div>
          <a href="{job['url']}"
             style="display:inline-block;margin-left:32px;padding:8px 18px;
                    background:#0a66c2;color:#fff;border-radius:8px;
                    text-decoration:none;font-size:13px;font-weight:600;">
             View on LinkedIn →
          </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:32px auto;padding:0 16px;">
    <div style="background:linear-gradient(135deg,#0a66c2,#0052a3);
                border-radius:16px 16px 0 0;padding:28px 32px;">
      <div style="color:#fff;font-size:22px;font-weight:700;">🎯 Daily Job Alert</div>
      <div style="color:#bfdbfe;font-size:14px;margin-top:4px;">
        {today} · AI/ML · NLP · Data Science · Munich
      </div>
    </div>
    <div style="background:#f3f4f6;padding:24px 0;">
      <div style="color:#6b7280;font-size:13px;margin-bottom:16px;">
        Top <strong>{len(jobs)}</strong> new positions found today
      </div>
      {cards}
    </div>
    <div style="text-align:center;padding:16px 0 32px;color:#9ca3af;font-size:12px;">
      Sang Hyeon Lee · Job Alert · Powered by python-jobspy + GitHub Actions
    </div>
  </div>
</body>
</html>"""


def send_email(html: str, job_count: int):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {job_count} New Jobs Today – AI/ML Munich ({datetime.now().strftime('%b %d')})"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())


def main():
    print(f"[{datetime.now().isoformat()}] Starting LinkedIn job alert...")

    seen_jobs = load_seen_jobs()
    print(f"  Previously seen: {len(seen_jobs)} jobs")

    raw_jobs = search_jobs()
    print(f"  Found {len(raw_jobs)} unique jobs")

    new_jobs = [j for j in raw_jobs if j["job_id"] not in seen_jobs]
    print(f"  New (unseen): {len(new_jobs)}")

    if not new_jobs:
        print("  No new jobs today — skipping email.")
        return

    top = new_jobs[:TOP_N]
    html = build_email_html(top)

    if not GMAIL_APP_PASSWORD:
        preview = Path("/tmp/job_alert_preview.html")
        preview.write_text(html)
        print(f"[ERROR] GMAIL_APP_PASSWORD not set. Preview saved to {preview}")
        sys.exit(1)

    send_email(html, len(top))
    print(f"  ✓ Email sent to {RECIPIENT_EMAIL}")

    seen_jobs.update(j["job_id"] for j in new_jobs)
    save_seen_jobs(seen_jobs)
    print("  Seen jobs DB updated.")


if __name__ == "__main__":
    main()
