# DisclosureFlow — requester intake portal

The one requester-facing surface (brief §11). A member of the public submits a
FOIA request; the portal validates it against the **real shared contract**
(`shared.contracts.Request`), assigns a tracking number, logs it, and produces
the exact **case-start payload** the Maestro Case expects. It also serves a
minimal status page (`/status/<tracking#>`).

Single file: `app.py` (Flask + the repo's `shared/` contracts). ~1 dependency.

## Run

```bash
# from repo root — Flask into the root venv (pydantic is already there)
uv pip install flask
cd portal
uv run python app.py          # → http://127.0.0.1:5000
```

Open <http://127.0.0.1:5000>. Click **Use sample request** to load the Journey-B
vague request, or type your own.

## Demo flow (on camera)

1. Show the portal. Narrate: *"This is where a member of the public requests
   records."* Click **Use sample request** (loads the vague Journey-B ask) → **Submit request**.
2. The confirmation shows a **tracking number** + *"routed to the agency's intake."*
   Narrate: *"A case opens and the 20-working-day clock starts."*
3. Expand **Operator: case-start payload** → copy the JSON.
4. In Studio Web → start the `DisclosureFlow` case (**Debug on cloud**) and paste
   those fields into the start inputs. The case runs Journey B from there
   (→ vague → clarification beat → …).

`/status/<tracking#>` shows stage-only status (the requester's view).

## Wiring: two modes

### Mode 1 — handoff (DEFAULT, zero platform risk)
The confirmation screen prints the canonical `Request` + identity payload; the
operator pastes it into the Maestro case-start inputs. Nothing to configure. The
`case_id` is a placeholder — Maestro auto-generates the real `CaseId` and ignores
the literal (case-model-spec §11), so bind the case's `case_id` input to the
`CaseId` system variable.

### Mode 2 — direct start (OPTIONAL, only with a verified endpoint)
If you have a working Maestro/Orchestrator **case-start endpoint** + token, set
these env vars and the portal POSTs the payload on submit (no copy-paste):

```bash
export MAESTRO_START_URL="https://.../<the case-start endpoint>"
export MAESTRO_START_TOKEN="<bearer token from: uipath auth --staging>"
export MAESTRO_FOLDER_KEY="257dab65-2353-4e0c-96e8-ff9f3746d9ed"   # Shared folder
uv run python app.py
```

On success the confirmation shows *"Case opened automatically."* On any failure it
falls back to showing the payload (Mode 1) — the demo can't break. The exact
case-start URL/verb is **unverified** (the requester→Maestro start path was the
flagged risk); have the platform-integrator confirm it before relying on Mode 2.
The call is plain stdlib `urllib`, so adding it needs no new dependency.

## Notes
- `intake/` holds one JSON per submission (audit + status lookup). It's runtime
  data — gitignored.
- The portal imports `shared/` directly, so it cannot drift from the agents'
  contract: a malformed request fails at the boundary, not silently.
