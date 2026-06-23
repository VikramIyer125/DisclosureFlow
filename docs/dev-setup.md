# Dev environment setup — `uv` + the `uipath` CLI

How to set up a fresh machine to build, run, and deploy DisclosureFlow. By the end you'll be able to run the tests, run an agent locally against a live LLM, and publish/invoke an agent on the UiPath staging tenant.

**Key idea:** there is **no global `uipath` install**. The `uipath` CLI ships as part of each agent's `uipath-langchain` dependency and is run with **`uv run uipath ...`** from inside an agent directory. `uv` manages Python and every virtualenv for you.

---

## 0. Prerequisites
- **git**, and the repo on the machine (clone it, or pull your working branch — currently `feature/contracts-seams-backbone`).
- **`make`** (preinstalled on macOS/Linux; on Windows use WSL or Git Bash, or run the underlying commands by hand).
- A browser (for the interactive UiPath login).
- Your **Anthropic API key** and access to the **same UiPath account** (staging tenant `hackathon26_632` / `DefaultTenant`).

---

## 1. Install `uv`

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or, on a Mac with Homebrew:  brew install uv
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart your shell, then verify:
```bash
uv --version        # expect uv 0.11.x or newer
```

`uv` will download and manage the right **Python** itself (the agents require ≥3.11) — you don't need to install Python separately. If you want to pin it: `uv python install 3.12`.

---

## 2. Get the `uipath` CLI working (per-agent venv)

The CLI lives in each agent's environment. Sync one agent's venv from its locked deps, then check the CLI:

```bash
cd agents/scoping-agent
uv sync                       # creates .venv from pyproject.toml + uv.lock
uv run uipath --version       # expect: uipath version 2.11.x
```

Repeat `uv sync` in the other two agent dirs when you start working in them (`agents/custodian-search-agent`, `agents/review-redaction-agent`). **Always prefix UiPath commands with `uv run`** so they use the agent's venv:

```bash
uv run uipath --help
```

---

## 3. Secrets — the `.env` file

The repo-root **`.env` is gitignored** (it holds secrets) so it is **not** on the new machine. It must contain these five keys:

```dotenv
ANTHROPIC_API_KEY=...        # your Anthropic key (for the agents' LLM calls)
UIPATH_ACCESS_TOKEN=...      # filled/refreshed by `uipath auth` (step 4); ~1h TTL
UIPATH_URL=...               # https://staging.uipath.com/<org>/<tenant>/orchestrator_/
UIPATH_TENANT_ID=...
UIPATH_ORGANIZATION_ID=...
```

**Two ways to get it:**
- **Recommended — copy it over.** Securely transfer the `.env` from your other device (it already has all five). Then refresh the short-lived token with step 4.
- **Recreate it.** Run step 4 (UiPath auth) to obtain the UiPath credentials, and add your `ANTHROPIC_API_KEY` line yourself.

> Note: `uv run uipath run ...` loads the **agent directory's** `.env`, which is empty. For a live local run, source the **root** `.env` first (shown in step 6).

---

## 4. Authenticate to UiPath (staging)

From inside any agent dir (so the CLI is available), run the interactive login — it opens a browser; sign in with the **same account**:

```bash
cd agents/scoping-agent
uv run uipath auth --staging
```

This stores credentials locally (under `.uipath/`) and gives you a working token. **The token is ~1 hour-lived** — if a publish/invoke later returns a 401, just re-run this command. Keep the root `.env`'s `UIPATH_ACCESS_TOKEN` in sync with the freshest token (copy it from `.uipath/.auth.json` if a script reads it from `.env`).

Verify auth works (lists processes in the shared folder; empty/`[]` is fine — it means you're authenticated):
```bash
uv run uipath assets list --folder-path "Shared"
```

---

## 5. Root environment (for tests, `steps/`, `shared/`)

The repo root has **no `pyproject.toml`** — the tests and the `steps/`/`shared/` code run against a simple root venv. Create it once:

```bash
# from the repo root
uv venv
uv pip install 'pydantic>=2,<3' pytest
```

---

## 6. Verify the whole setup

```bash
# (a) deterministic tests — no cloud, no LLM
uv run python -m pytest tests/ -q                 # expect: 18 passed

# (b) run an agent locally against the live LLM
cd agents/review-redaction-agent
set -a && . ../../.env && set +a                  # load keys into the run env (bash/zsh)
uv run uipath run agent --file fixtures/records_clean.json
#   -> a ScopedRequest/ReviewResult prints; "Successful execution."

# (c) confirm you can see the cloud deployments
uv run uipath assets list --folder-path "Shared"  # authenticated call succeeds
```

On Windows PowerShell, replace the `set -a && . ../../.env && set +a` line by loading the `.env` another way (e.g. `Get-Content ..\..\.env | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1],$matches[2]) } }`).

---

## 7. Build & deploy (once the above works)

From the **repo root**, the Makefile vendors `shared/` (and `policy-packs/` for the Review agent) into the agent before packing — always build through it:

```bash
make pack    AGENT=scoping-agent     # vendor shared/ -> uipath init -> uipath pack
make publish AGENT=scoping-agent     # ... -> uipath publish to Orchestrator
```

See **`PROJECT_STATUS.md`** ("How to build, run, test, deploy" + the platform gotchas) and each agent's **`AGENTS.md`** for details.

---

## Troubleshooting
- **`401` on publish/invoke** → token expired; re-run `uv run uipath auth --staging` and update `.env`'s `UIPATH_ACCESS_TOKEN`.
- **`uipath: command not found`** → you forgot the `uv run` prefix, or you're not in an agent dir whose venv has been `uv sync`'d.
- **Agent run raises `...UnrecoverableError: ANTHROPIC_API_KEY not configured`** → you didn't source the root `.env` (step 6b), or the key name is misspelled in `.env` (it must be exactly `ANTHROPIC_API_KEY`).
- **`make: command not found` (Windows)** → use WSL/Git Bash, or run the `uv run uipath ...` steps the Makefile target wraps by hand.
- **A warning about an unsupported Python request (e.g. `icl2`)** from a stray `.python-version` in a parent folder is harmless — ignore it.
- **Raw `curl` calls to Orchestrator get blocked (code 1010)** → the edge WAF blocks them; use the CLI/SDK (which go over the supported transport).
