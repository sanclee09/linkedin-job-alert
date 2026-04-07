"""Unit tests for job_alert.py"""

import json
import tempfile
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

import job_alert


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------
class TestNormalizeTitle:
    def test_strips_gender_mwd(self):
        assert job_alert._normalize_title("AI Engineer (m/w/d)") == "ai engineer"

    def test_strips_gender_fmd(self):
        assert job_alert._normalize_title("Data Scientist (f/m/d)") == "data scientist"

    def test_strips_gender_wmd(self):
        assert job_alert._normalize_title("ML Engineer (w/m/d)") == "ml engineer"

    def test_strips_all_genders(self):
        assert job_alert._normalize_title("Data Engineer (all genders)") == "data engineer"

    def test_strips_location_suffix_dash(self):
        result = job_alert._normalize_title("AI Engineer (m/w/d) - München oder Mobile Office")
        assert result == "ai engineer"

    def test_strips_location_suffix_endash(self):
        result = job_alert._normalize_title("AI Software Engineer – Agentic Systems & RAG (w/m/d)")
        assert result == "ai software engineer"

    def test_preserves_core_title(self):
        assert job_alert._normalize_title("Founding ML Engineer") == "founding ml engineer"

    def test_strips_whitespace(self):
        assert job_alert._normalize_title("  AI Engineer  (m/w/d)  ") == "ai engineer"

    def test_empty_string(self):
        assert job_alert._normalize_title("") == ""

    def test_no_gender_no_location(self):
        assert job_alert._normalize_title("Datenwissenschaftler") == "datenwissenschaftler"

    def test_multiple_gender_tags(self):
        result = job_alert._normalize_title("Engineer (m/f/d) (all genders)")
        assert result == "engineer"


# ---------------------------------------------------------------------------
# _job_key
# ---------------------------------------------------------------------------
class TestJobKey:
    def test_basic(self):
        assert job_alert._job_key("AI Engineer", "BMW") == "ai engineer@bmw"

    def test_strips_whitespace(self):
        assert job_alert._job_key("  AI Engineer ", " BMW ") == "ai engineer@bmw"

    def test_lowercases(self):
        assert job_alert._job_key("Senior ML Engineer", "Celonis") == "senior ml engineer@celonis"


# ---------------------------------------------------------------------------
# _decode_header
# ---------------------------------------------------------------------------
class TestDecodeHeader:
    def test_plain_string(self):
        assert job_alert._decode_header("Hello World") == "Hello World"

    def test_encoded_utf8(self):
        # Simulate an encoded header: =?utf-8?b?<base64>?=
        import email.header
        encoded = email.header.make_header(
            [(b"M\xc3\xbcnchen", "utf-8")]
        ).encode()
        assert "München" in job_alert._decode_header(encoded)


# ---------------------------------------------------------------------------
# load_seen_jobs / save_seen_jobs
# ---------------------------------------------------------------------------
class TestSeenJobs:
    def test_load_empty_when_no_file(self, tmp_path):
        with mock.patch.object(job_alert, "SEEN_JOBS_FILE", tmp_path / "nonexistent.json"):
            assert job_alert.load_seen_jobs() == set()

    def test_save_and_load_roundtrip(self, tmp_path):
        filepath = tmp_path / "seen.json"
        with mock.patch.object(job_alert, "SEEN_JOBS_FILE", filepath):
            job_alert.save_seen_jobs({"id1", "id2", "id3"})
            loaded = job_alert.load_seen_jobs()
            assert loaded == {"id1", "id2", "id3"}

    def test_save_creates_parent_dirs(self, tmp_path):
        filepath = tmp_path / "sub" / "dir" / "seen.json"
        with mock.patch.object(job_alert, "SEEN_JOBS_FILE", filepath):
            job_alert.save_seen_jobs({"id1"})
            assert filepath.exists()


# ---------------------------------------------------------------------------
# load_applied_jobs / save_applied_jobs
# ---------------------------------------------------------------------------
class TestAppliedJobs:
    def test_load_empty_when_no_file(self, tmp_path):
        with mock.patch.object(job_alert, "APPLIED_JOBS_FILE", tmp_path / "nonexistent.json"):
            assert job_alert.load_applied_jobs() == set()

    def test_save_and_load_roundtrip(self, tmp_path):
        filepath = tmp_path / "applied.json"
        with mock.patch.object(job_alert, "APPLIED_JOBS_FILE", filepath):
            job_alert.save_applied_jobs({"key1", "key2"})
            loaded = job_alert.load_applied_jobs()
            assert loaded == {"key1", "key2"}


# ---------------------------------------------------------------------------
# detect_work_type
# ---------------------------------------------------------------------------
class TestDetectWorkType:
    def test_remote_flag(self):
        row = pd.Series({"is_remote": True, "location": "Munich", "description": ""})
        assert job_alert.detect_work_type(row) == "Remote"

    def test_hybrid_in_location(self):
        row = pd.Series({"is_remote": False, "location": "Munich (Hybrid)", "description": ""})
        assert job_alert.detect_work_type(row) == "Hybrid"

    def test_hybrid_in_description(self):
        row = pd.Series({"is_remote": False, "location": "Munich", "description": "This is a hybrid role"})
        assert job_alert.detect_work_type(row) == "Hybrid"

    def test_remote_in_location(self):
        row = pd.Series({"is_remote": False, "location": "Remote / Munich", "description": ""})
        assert job_alert.detect_work_type(row) == "Remote"

    def test_remote_in_description(self):
        row = pd.Series({"is_remote": False, "location": "Munich", "description": "Fully remote position"})
        assert job_alert.detect_work_type(row) == "Remote"

    def test_onsite_default(self):
        row = pd.Series({"is_remote": False, "location": "Munich, Germany", "description": "Work in office"})
        assert job_alert.detect_work_type(row) == "On-site"

    def test_missing_fields(self):
        row = pd.Series({})
        assert job_alert.detect_work_type(row) == "On-site"

    def test_hybrid_takes_priority_over_remote_in_desc(self):
        row = pd.Series({"is_remote": False, "location": "Hybrid Munich", "description": ""})
        assert job_alert.detect_work_type(row) == "Hybrid"


# ---------------------------------------------------------------------------
# build_email_html
# ---------------------------------------------------------------------------
class TestBuildEmailHtml:
    def test_returns_html_with_jobs(self):
        jobs = [
            {
                "title": "AI Engineer",
                "company": "TestCo",
                "location": "Munich",
                "work_type": "Remote",
                "description": "Build AI systems",
                "url": "https://linkedin.com/jobs/view/123",
            }
        ]
        html = job_alert.build_email_html(jobs)
        assert "<!DOCTYPE html>" in html
        assert "AI Engineer" in html
        assert "TestCo" in html
        assert "Remote" in html
        assert "https://linkedin.com/jobs/view/123" in html

    def test_escapes_html_chars(self):
        jobs = [
            {
                "title": "Engineer <script>",
                "company": "A&B Corp",
                "location": "Munich <city>",
                "work_type": "On-site",
                "description": "Test & verify <systems>",
                "url": "https://example.com",
            }
        ]
        html = job_alert.build_email_html(jobs)
        assert "<script>" not in html
        assert "&amp;" in html
        assert "&lt;" in html

    def test_truncates_long_description(self):
        jobs = [
            {
                "title": "Engineer",
                "company": "Co",
                "location": "Munich",
                "work_type": "On-site",
                "description": "A" * 300,
                "url": "https://example.com",
            }
        ]
        html = job_alert.build_email_html(jobs)
        assert "\u2026" in html  # ellipsis character

    def test_empty_jobs_list(self):
        html = job_alert.build_email_html([])
        assert "<!DOCTYPE html>" in html
        assert "Top <strong>0</strong>" in html

    def test_multiple_jobs_numbered(self):
        jobs = [
            {
                "title": f"Job {i}",
                "company": "Co",
                "location": "Munich",
                "work_type": "On-site",
                "description": "Desc",
                "url": "https://example.com",
            }
            for i in range(3)
        ]
        html = job_alert.build_email_html(jobs)
        assert ">1<" in html
        assert ">2<" in html
        assert ">3<" in html

    def test_work_type_badges(self):
        for wt in ["Remote", "Hybrid", "On-site"]:
            jobs = [
                {
                    "title": "Eng",
                    "company": "Co",
                    "location": "Munich",
                    "work_type": wt,
                    "description": "Desc",
                    "url": "https://example.com",
                }
            ]
            html = job_alert.build_email_html(jobs)
            assert wt in html


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------
class TestSendEmail:
    @mock.patch("job_alert.smtplib.SMTP_SSL")
    def test_sends_email(self, mock_smtp_class):
        mock_smtp = mock.MagicMock()
        mock_smtp_class.return_value.__enter__ = mock.Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test_pass"):
            job_alert.send_email("<html>test</html>", 5)

        mock_smtp.login.assert_called_once_with(job_alert.SENDER_EMAIL, "test_pass")
        mock_smtp.sendmail.assert_called_once()
        args = mock_smtp.sendmail.call_args[0]
        assert args[0] == job_alert.SENDER_EMAIL
        assert args[1] == job_alert.RECIPIENT_EMAIL

    @mock.patch("job_alert.smtplib.SMTP_SSL")
    def test_email_subject_contains_count(self, mock_smtp_class):
        mock_smtp = mock.MagicMock()
        mock_smtp_class.return_value.__enter__ = mock.Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test_pass"):
            job_alert.send_email("<html>test</html>", 7)

        raw_msg = mock_smtp.sendmail.call_args[0][2]
        # Subject is base64-encoded due to emoji, so parse it back
        import email as email_lib
        parsed = email_lib.message_from_string(raw_msg)
        subject = str(email_lib.header.decode_header(parsed["Subject"])[0][0], "utf-8")
        assert "7 New Jobs Today" in subject


# ---------------------------------------------------------------------------
# fetch_applied_jobs_from_gmail
# ---------------------------------------------------------------------------
class TestFetchAppliedJobsFromGmail:
    def test_returns_empty_when_no_password(self):
        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", ""):
            assert job_alert.fetch_applied_jobs_from_gmail() == set()

    @mock.patch("job_alert.imaplib.IMAP4_SSL")
    def test_extracts_linkedin_title(self, mock_imap_class):
        mock_mail = mock.MagicMock()
        mock_imap_class.return_value = mock_mail

        # Build a fake LinkedIn confirmation email
        msg = MIMEMultipart()
        msg["Subject"] = "Sang Hyeon, your application was sent to TestCo"
        msg["From"] = "LinkedIn <jobs-noreply@linkedin.com>"
        html_body = '''
        <html><body>
        <a href="https://linkedin.com/comm/jobs/view/123"><img src="logo.png"/></a>
        <a href="https://linkedin.com/comm/jobs/view/123">AI Engineer (m/w/d)</a>
        </body></html>
        '''
        msg.attach(MIMEText(html_body, "html"))

        # First search (LinkedIn) returns one message
        mock_mail.search.side_effect = [
            ("OK", [b"1"]),
            ("OK", [b""]),  # second search (direct apps) returns nothing
        ]
        mock_mail.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])

        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test"):
            result = job_alert.fetch_applied_jobs_from_gmail()

        assert "ai engineer" in result

    @mock.patch("job_alert.imaplib.IMAP4_SSL")
    def test_extracts_direct_bewerbung_title(self, mock_imap_class):
        mock_mail = mock.MagicMock()
        mock_imap_class.return_value = mock_mail

        # Build a fake direct application email (header only)
        msg = MIMEMultipart()
        msg["Subject"] = "Deine Bewerbung als Data Scientist (m/w/d) - München"
        msg["From"] = "HR <hr@company.de>"

        # First search (LinkedIn) returns nothing
        # Second search (direct) returns one message
        mock_mail.search.side_effect = [
            ("OK", [b""]),
            ("OK", [b"2"]),
        ]
        mock_mail.fetch.return_value = ("OK", [(b"2", msg.as_bytes())])

        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test"):
            result = job_alert.fetch_applied_jobs_from_gmail()

        assert "data scientist" in result

    @mock.patch("job_alert.imaplib.IMAP4_SSL")
    def test_skips_own_replies(self, mock_imap_class):
        mock_mail = mock.MagicMock()
        mock_imap_class.return_value = mock_mail

        msg = MIMEMultipart()
        msg["Subject"] = "Re: Deine Bewerbung als AI Engineer (m/w/d)"
        msg["From"] = f"Sang Hyeon Lee <{job_alert.SENDER_EMAIL}>"

        mock_mail.search.side_effect = [
            ("OK", [b""]),
            ("OK", [b"3"]),
        ]
        mock_mail.fetch.return_value = ("OK", [(b"3", msg.as_bytes())])

        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test"):
            result = job_alert.fetch_applied_jobs_from_gmail()

        assert len(result) == 0

    @mock.patch("job_alert.imaplib.IMAP4_SSL")
    def test_handles_imap_error_gracefully(self, mock_imap_class):
        mock_imap_class.side_effect = Exception("Connection refused")
        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test"):
            result = job_alert.fetch_applied_jobs_from_gmail()
        assert result == set()


# ---------------------------------------------------------------------------
# search_jobs (with mocked scrape_jobs and IMAP)
# ---------------------------------------------------------------------------
class TestSearchJobs:
    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs")
    def test_excludes_senior_titles(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "1", "title": "Senior AI Engineer", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
            {"id": "2", "title": "AI Engineer", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        titles = [j["title"] for j in result]
        assert "AI Engineer" in titles
        assert "Senior AI Engineer" not in titles

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs")
    def test_excludes_working_student(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "1", "title": "Working Student ML", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
            {"id": "2", "title": "ML Engineer", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        titles = [j["title"] for j in result]
        assert "ML Engineer" in titles
        assert "Working Student ML" not in titles

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs")
    def test_excludes_bi_titles(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "1", "title": "Business Intelligence Analyst", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
            {"id": "2", "title": "Data Scientist", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        titles = [j["title"] for j in result]
        assert "Data Scientist" in titles
        assert "Business Intelligence Analyst" not in titles

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value={"ai engineer"})
    @mock.patch("job_alert.scrape_jobs")
    def test_filters_applied_jobs(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "1", "title": "AI Engineer (m/w/d)", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
            {"id": "2", "title": "Data Scientist", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        titles = [j["title"] for j in result]
        assert "Data Scientist" in titles
        assert "AI Engineer (m/w/d)" not in titles

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs")
    def test_deduplicates_by_job_id(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "same-id", "title": "AI Engineer", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
            {"id": "same-id", "title": "AI Engineer", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "desc", "job_url": "https://example.com", "date_posted": None},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        assert len(result) == 1

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs")
    def test_sorts_by_date_newest_first(self, mock_scrape, mock_gmail):
        df = pd.DataFrame([
            {"id": "1", "title": "Old Job", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "d", "job_url": "https://example.com",
             "date_posted": datetime(2026, 4, 1)},
            {"id": "2", "title": "New Job", "company": "Co", "location": "Munich",
             "is_remote": False, "description": "d", "job_url": "https://example.com",
             "date_posted": datetime(2026, 4, 5)},
        ])
        mock_scrape.return_value = df

        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()

        assert result[0]["title"] == "New Job"
        assert result[1]["title"] == "Old Job"

    @mock.patch("job_alert.fetch_applied_jobs_from_gmail", return_value=set())
    @mock.patch("job_alert.scrape_jobs", side_effect=Exception("Network error"))
    def test_handles_scrape_error(self, mock_scrape, mock_gmail):
        with mock.patch.object(job_alert, "SEARCH_QUERIES", ["test"]):
            result = job_alert.search_jobs()
        assert result == []


# ---------------------------------------------------------------------------
# main (integration-level)
# ---------------------------------------------------------------------------
class TestMain:
    @mock.patch("job_alert.send_email")
    @mock.patch("job_alert.search_jobs")
    @mock.patch("job_alert.save_seen_jobs")
    @mock.patch("job_alert.load_seen_jobs", return_value=set())
    def test_sends_email_when_new_jobs(self, mock_load, mock_save, mock_search, mock_send):
        mock_search.return_value = [
            {"job_id": "1", "title": "AI Eng", "company": "Co", "location": "Munich",
             "work_type": "Remote", "description": "d", "url": "https://example.com", "date_posted": None}
        ]
        with mock.patch.object(job_alert, "GMAIL_APP_PASSWORD", "test"):
            job_alert.main()
        mock_send.assert_called_once()
        mock_save.assert_called_once()

    @mock.patch("job_alert.send_email")
    @mock.patch("job_alert.search_jobs", return_value=[])
    @mock.patch("job_alert.load_seen_jobs", return_value=set())
    def test_skips_email_when_no_jobs(self, mock_load, mock_search, mock_send):
        job_alert.main()
        mock_send.assert_not_called()

    @mock.patch("job_alert.send_email")
    @mock.patch("job_alert.search_jobs")
    @mock.patch("job_alert.load_seen_jobs")
    def test_skips_already_seen_jobs(self, mock_load, mock_search, mock_send):
        mock_load.return_value = {"1"}
        mock_search.return_value = [
            {"job_id": "1", "title": "AI Eng", "company": "Co", "location": "Munich",
             "work_type": "Remote", "description": "d", "url": "https://example.com", "date_posted": None}
        ]
        job_alert.main()
        mock_send.assert_not_called()
