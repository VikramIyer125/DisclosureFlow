"""DisclosureFlow — minimal requester intake portal.

The ONE requester-facing surface (brief §11): a member of the public submits a
FOIA request here. On submit we build the canonical `shared.contracts.Request`
(so the portal speaks the same contract language as the agents and the case
model), assign a tracking id, log it, and produce the exact **case-start
payload** the Maestro Case expects.

Two wiring modes (see portal/README.md):
  * DEFAULT — "handoff": the confirmation screen shows the case-start JSON; the
    operator pastes it into Studio Web "Debug on cloud" to open the case. Zero
    platform dependency — cannot break the demo.
  * OPTIONAL — "direct start": if env MAESTRO_START_URL (+ MAESTRO_START_TOKEN)
    is set, the portal POSTs the payload to start the case automatically. Uses
    only stdlib urllib so the portal stays a one-dependency app.

Run:  cd portal && python app.py     (then open http://localhost:5000)
Deps: flask + pydantic (pydantic already in the repo's root venv).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, redirect, url_for, render_template_string, abort

# Make the repo-root `shared/` package importable when run from portal/.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.contracts import Request  # noqa: E402  (after sys.path insert)

JURISDICTION = "federal_foia"
INTAKE_DIR = Path(__file__).resolve().parent / "intake"
INTAKE_DIR.mkdir(exist_ok=True)

# Pre-fillable demo request (the Journey-B vague request → triggers the
# clarification beat). Surfaced behind a "Use sample request" button.
SAMPLE_REQUESTER = "curious.citizen@example.org"
SAMPLE_TEXT = "I want all records about the agency's spending and any problems it has had."

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Case-start handoff
# ─────────────────────────────────────────────────────────────────────────────
def case_start_payload(req: Request) -> dict:
    """The exact JSON the Maestro Case start form / scoping Service Task expects.

    Request fields + the two sibling identity fields. `case_id` is a placeholder:
    Maestro auto-generates the real `CaseId` and ignores this literal (§11), so
    binding the case_id input to the CaseId system variable is what matters.
    """
    return {
        **req.model_dump(mode="json"),
        "case_id": "intake-unassigned",
        "jurisdiction": JURISDICTION,
    }


def try_direct_start(payload: dict) -> str | None:
    """If MAESTRO_START_URL is configured, POST the payload to start the case.

    Returns a short status string on success, or None when not configured.
    Raises on a configured-but-failed call so the operator sees the error.
    """
    url = os.environ.get("MAESTRO_START_URL")
    if not url:
        return None
    token = os.environ.get("MAESTRO_START_TOKEN", "")
    folder_key = os.environ.get("MAESTRO_FOLDER_KEY", "")
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if folder_key:
        headers["x-uipath-folderkey"] = folder_key
    http_req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(http_req, timeout=20) as resp:  # noqa: S310 (configured URL)
        return f"case-start POST {resp.status}"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(FORM_HTML, sample_requester=SAMPLE_REQUESTER, sample_text=SAMPLE_TEXT)


@app.route("/submit", methods=["POST"])
def submit():
    requester = (request.form.get("requester") or "").strip()
    text = (request.form.get("text") or "").strip()
    if not requester or not text:
        abort(400, "Both an email and a request description are required.")

    req = Request(
        request_id=f"REQ-{uuid.uuid4().hex[:8].upper()}",
        requester=requester,
        text=text,
        submitted_at=datetime.now(timezone.utc),
        attachments=[],
    )

    payload = case_start_payload(req)
    record = {"request": req.model_dump(mode="json"), "case_start_payload": payload, "status": "received"}

    # Idempotent-ish persistence keyed by request_id (audit + status lookup).
    direct = None
    try:
        direct = try_direct_start(payload)
        if direct:
            record["status"] = "case_opened"
            record["direct_start"] = direct
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        record["direct_start_error"] = str(exc)

    (INTAKE_DIR / f"{req.request_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    return render_template_string(
        CONFIRM_HTML,
        request_id=req.request_id,
        requester=req.requester,
        submitted_at=req.submitted_at.strftime("%Y-%m-%d %H:%M UTC"),
        payload_json=json.dumps(payload, indent=2),
        direct=direct,
        direct_error=record.get("direct_start_error"),
    )


@app.route("/status/<request_id>")
def status(request_id: str):
    path = INTAKE_DIR / f"{request_id}.json"
    if not path.exists():
        abort(404, "No request found with that tracking number.")
    rec = json.loads(path.read_text(encoding="utf-8"))
    stage = {
        "received": "Received — queued for the agency's intake & scoping.",
        "case_opened": "Case opened — the agency is processing your request.",
    }.get(rec.get("status", "received"), "Received.")
    return render_template_string(
        STATUS_HTML, request_id=request_id, stage=stage, text=rec["request"]["text"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Templates (inline — single-file app)
# ─────────────────────────────────────────────────────────────────────────────
BASE_CSS = """
  :root { --ink:#1a2433; --accent:#1b4f72; --line:#d6dde6; --bg:#f4f6f9; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); background:var(--bg); line-height:1.5; }
  .bar { background:var(--accent); color:#fff; padding:14px 0; }
  .bar .wrap { display:flex; align-items:center; gap:12px; }
  .seal { width:34px; height:34px; border-radius:50%; background:#fff; color:var(--accent);
          display:flex; align-items:center; justify-content:center; font-weight:700; }
  .wrap { max-width:760px; margin:0 auto; padding:0 22px; }
  main { padding:28px 0 60px; }
  h1 { font-size:1.5rem; margin:.2rem 0; }
  .sub { color:#5a6b7b; margin-top:0; }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:22px; margin-top:18px; }
  label { display:block; font-weight:600; margin:14px 0 6px; }
  input[type=email], textarea { width:100%; padding:11px 12px; border:1px solid var(--line);
         border-radius:7px; font-size:1rem; font-family:inherit; }
  textarea { min-height:120px; resize:vertical; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:18px; }
  button, .btn { background:var(--accent); color:#fff; border:0; padding:11px 18px; border-radius:7px;
         font-size:1rem; cursor:pointer; text-decoration:none; display:inline-block; }
  .btn.ghost { background:#eef2f6; color:var(--accent); }
  .note { font-size:.86rem; color:#5a6b7b; }
  .pill { display:inline-block; background:#e8f0f7; color:var(--accent); border-radius:999px;
          padding:3px 11px; font-size:.8rem; font-weight:600; }
  pre { background:#0f1b2a; color:#cfe3f5; padding:14px; border-radius:8px; overflow:auto; font-size:.82rem; }
  details summary { cursor:pointer; font-weight:600; color:var(--accent); }
  .ok { color:#1e7a46; font-weight:600; }
  .warn { color:#9a5b00; }
  code { background:#eef2f6; padding:1px 5px; border-radius:4px; }
"""

_HEADER = """
  <div class="bar"><div class="wrap"><div class="seal">FOIA</div>
    <div><strong>DisclosureFlow</strong> &middot; Public Records Request Portal</div></div></div>
"""

FORM_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Submit a FOIA Request</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<style>" + BASE_CSS + "</style></head><body>" + _HEADER +
    """
    <main><div class="wrap">
      <span class="pill">Freedom of Information Act</span>
      <h1>Request public records</h1>
      <p class="sub">Submit a request for agency records. Our default posture is <strong>disclosure</strong>:
      records are released unless a specific, human-approved exemption applies.</p>

      <form class="card" method="post" action="/submit">
        <label for="requester">Your email</label>
        <input id="requester" name="requester" type="email" required placeholder="you@example.org"
               value="{{ '' }}">

        <label for="text">What records are you requesting?</label>
        <textarea id="text" name="text" required
          placeholder="Describe the records you want — include a subject, a time period, and the office if you can."></textarea>
        <p class="note">Tip: the more specific (subject, dates, office), the faster we can fulfill it.
        Vague requests come back to you with a suggested narrowing.</p>

        <div class="row">
          <button type="submit">Submit request</button>
          <button type="button" class="btn ghost" onclick="loadSample()">Use sample request</button>
        </div>
      </form>
      <p class="note">By submitting, a case is opened and the statutory 20-working-day clock starts.</p>
    </div></main>

    <script>
      function loadSample(){
        document.getElementById('requester').value = {{ sample_requester|tojson }};
        document.getElementById('text').value = {{ sample_text|tojson }};
      }
    </script>
    </body></html>
    """
)

CONFIRM_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Request received</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<style>" + BASE_CSS + "</style></head><body>" + _HEADER +
    """
    <main><div class="wrap">
      <span class="pill">Request received</span>
      <h1>Thank you — your request is in.</h1>
      <p class="sub">Tracking number <strong>{{ request_id }}</strong> &middot; submitted {{ submitted_at }}
      &middot; for {{ requester }}</p>

      <div class="card">
        <p class="ok">✓ Your request has been logged and routed to the agency's intake &amp; scoping.</p>
        {% if direct %}<p class="ok">✓ Case opened automatically ({{ direct }}).</p>
        {% elif direct_error %}<p class="warn">⚠ Auto-start not available ({{ direct_error }}). Operator opens the case from the payload below.</p>
        {% endif %}
        <p>Track status any time at
          <a href="/status/{{ request_id }}"><code>/status/{{ request_id }}</code></a>.</p>
      </div>

      <details class="card">
        <summary>Operator: case-start payload (paste into Studio Web “Debug on cloud”)</summary>
        <p class="note">The canonical <code>Request</code> + identity fields the Maestro Case expects.
        <code>case_id</code> is a placeholder — Maestro auto-generates the real <code>CaseId</code>.</p>
        <pre>{{ payload_json }}</pre>
      </details>

      <p class="row"><a class="btn ghost" href="/">← Submit another request</a></p>
    </div></main></body></html>
    """
)

STATUS_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Request status</title>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<style>" + BASE_CSS + "</style></head><body>" + _HEADER +
    """
    <main><div class="wrap">
      <span class="pill">Status</span>
      <h1>Request {{ request_id }}</h1>
      <div class="card">
        <p><strong>Stage:</strong> {{ stage }}</p>
        <p class="note"><strong>Your request:</strong> {{ text }}</p>
      </div>
      <p class="row"><a class="btn ghost" href="/">← Back to the portal</a></p>
    </div></main></body></html>
    """
)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
