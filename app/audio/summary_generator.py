"""
SummaryGenerator — calls the Anthropic API to summarize a day's transcript
and writes the result as a self-contained HTML report.
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_REPORT_DIR = str(Path.home() / "Documents" / "NeoSCAN" / "Summaries")
MAX_TOKENS = 4096
HTTP_TIMEOUT_SEC = 120

AVAILABLE_MODELS = [
    ("Claude Opus 4.7",   "claude-opus-4-7"),
    ("Claude Sonnet 4.6", "claude-sonnet-4-6"),
    ("Claude Haiku 4.5",  "claude-haiku-4-5-20251001"),
]

_PROMPT_TEMPLATE = """\
You are summarizing a full day of two-way radio transmissions captured by a \
police/fire/EMS scanner. The transcript was produced by automatic speech \
recognition on short, often noisy audio clips, so individual entries may be \
garbled or fragmentary.

Date: {date}

Transcript:
{transcript}

Produce an HTML summary suitable for a daily report. Use only these tags: \
h2, h3, p, ul, li, strong, em. Do NOT include <html>, <body>, <head>, <style>, \
<script>, or code fences — return only the inner content that will be placed \
inside a <main> element.

Structure:
1. <h2>Overview</h2> — 3-5 sentences summarizing the day's radio activity \
(volume, prevailing systems, general tone).
2. <h2>Northborough</h2> — events occurring in Northborough, Massachusetts. \
In the transcripts Northborough is almost always written as the abbreviation \
"Northboro" — specifically "Northboro PD" (police), "Northboro FD" (fire / \
EMS), and "Northboro DPW" (public works). Treat any line tagged with one of \
those labels, or any mention of "Northboro" / "Northborough" by name, as \
Northborough activity. Also pull in entries that reference Northborough \
street names or landmarks (e.g. Main St, West Main St, Church St, Hudson St, \
Whitney St, Bartlett Pond, Assabet Reservoir) or units known to belong to \
Northborough. Use <ul><li> for individual events with approximate time and \
a brief description. If no Northborough activity is identifiable in the \
transcript, write a single <p> stating "No Northborough activity \
identified." Do not omit this section.
3. <h2>Notable Events</h2> — concrete incidents from elsewhere, grouped by \
topic/system using <h3> subheadings. Inside each subsection use <ul><li> for \
individual events. Quote brief snippets where they add clarity. Omit this \
section if nothing notable.
4. <h2>Flagged Keywords</h2> — if the transcript mentions medical emergencies, \
structure fires, vehicle pursuits, shots fired, MVAs, code 3 responses, \
hazmat, or similar high-priority terms (anywhere, including Northborough), \
list them as <ul><li> items with the approximate time. Omit this section \
entirely if none.

Be concise. Avoid filler like "the radio mentioned" or "operators discussed". \
Lead with concrete content. If the transcript is empty or unintelligible, \
return only a single <p> stating that.
"""


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NeoSCAN Daily Summary — {date}</title>
<style>
  :root {{
    color-scheme: light dark;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 760px;
    margin: 2rem auto;
    padding: 0 1.25rem;
    line-height: 1.55;
    color: #222;
    background: #fafafa;
  }}
  header {{
    border-bottom: 1px solid #ccc;
    margin-bottom: 1.5rem;
    padding-bottom: 0.75rem;
  }}
  h1 {{ font-size: 1.6rem; margin: 0; }}
  .meta {{ color: #777; font-size: 0.9rem; margin-top: 0.25rem; }}
  main h2 {{
    margin-top: 2rem;
    border-bottom: 1px solid #ddd;
    padding-bottom: 0.25rem;
  }}
  main h3 {{ margin-top: 1.25rem; color: #444; }}
  main ul {{ padding-left: 1.5rem; }}
  main li {{ margin: 0.25rem 0; }}
  footer {{
    margin-top: 3rem;
    border-top: 1px solid #ddd;
    padding-top: 0.75rem;
    color: #888;
    font-size: 0.8rem;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1a1a1a; color: #e6e6e6; }}
    main h2, main h3 {{ color: #f0f0f0; border-color: #333; }}
    header, footer {{ border-color: #333; }}
    .meta {{ color: #aaa; }}
  }}
</style>
</head>
<body>
<header>
  <h1>NeoSCAN Daily Summary</h1>
  <div class="meta">{date_long} · model {model} · generated {generated}</div>
</header>
<main>
{summary_html}
</main>
<footer>
  Generated from <code>{transcript_name}</code> by NeoSCAN.
</footer>
</body>
</html>
"""

_CODE_FENCE_RE = re.compile(r"^\s*```(?:html)?\s*\n?|\n?```\s*$", re.MULTILINE)


class SummaryError(Exception):
    """Raised when the Anthropic API call or report generation fails."""


class SummaryGenerator:
    def __init__(self, api_key: str, model: str, report_dir: str | Path) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.report_dir = Path(report_dir) if report_dir else Path(DEFAULT_REPORT_DIR)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def transcript_path(self, date: _dt.date, transcript_dir: str | Path) -> Path:
        return Path(transcript_dir) / f"{date.isoformat()}.txt"

    def report_path(self, date: _dt.date) -> Path:
        return self.report_dir / f"{date.isoformat()}.html"

    def needs_report(self, date: _dt.date, transcript_dir: str | Path) -> bool:
        """True iff a transcript exists for `date` but no report does yet."""
        return (
            self.transcript_path(date, transcript_dir).exists()
            and not self.report_path(date).exists()
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(self, date: _dt.date, transcript_dir: str | Path) -> Path:
        if not self.api_key:
            raise SummaryError("Anthropic API key is empty")

        tx_path = self.transcript_path(date, transcript_dir)
        if not tx_path.exists():
            raise SummaryError(f"Transcript not found: {tx_path}")

        transcript_text = tx_path.read_text(encoding="utf-8", errors="replace").strip()
        if not transcript_text:
            raise SummaryError(f"Transcript is empty: {tx_path}")

        prompt = _PROMPT_TEMPLATE.format(
            date=date.isoformat(),
            transcript=transcript_text,
        )

        body_html = self._call_anthropic(prompt)

        self.report_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.report_path(date)
        # %-d / %#d differ across platforms, so build the long date manually
        date_long = f"{date.strftime('%A, %B')} {date.day}, {date.year}"
        full_html = _HTML_TEMPLATE.format(
            date=date.isoformat(),
            date_long=date_long,
            model=html.escape(self.model),
            generated=_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            transcript_name=html.escape(tx_path.name),
            summary_html=body_html,
        )
        out_path.write_text(full_html, encoding="utf-8")
        log.info("SummaryGenerator: wrote %s", out_path)
        return out_path

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _call_anthropic(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=payload,
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            msg = _extract_api_error(err_body) or err_body[:500]
            raise SummaryError(f"Anthropic API error {exc.code}: {msg}") from exc
        except urllib.error.URLError as exc:
            raise SummaryError(f"Network error reaching Anthropic: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SummaryError(f"Could not parse Anthropic response: {exc}") from exc

        # Standard messages-API shape: {"content": [{"type": "text", "text": "..."}]}
        text_chunks = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        text = "\n".join(text_chunks).strip()
        if not text:
            raise SummaryError("Anthropic response contained no text content")

        # Strip ```html fences if the model added them despite the instructions.
        return _CODE_FENCE_RE.sub("", text).strip()


def _extract_api_error(body: str) -> str:
    """Pull the human-readable message out of an Anthropic error body."""
    try:
        data = json.loads(body)
        err = data.get("error", {})
        return err.get("message", "") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""
