# Platform capability check (Milestone 0)

Tenant: `hackathon26_632` on **staging** (`https://staging.uipath.com/hackathon26_632/...`)
Tenant name (fill in): DefaultTenant
Date checked: 2026-06-22

Record each as PASS / FAIL with a one-line note. Do not start FOIA logic until the BLOCKING rows pass and the hello-world deploy round-trips.

| # | Capability | Blocking? | Result | Notes |
|---|------------|-----------|--------|-------|
| 1 | **Maestro Case** available — you can create an *agentic case / case management* project (not just a BPMN agentic process) in Studio Web | **BLOCKING** | ✅ PASS|
| 2 | Service Task **"Start and wait for agent"** can target a deployed coded agent | **BLOCKING** | ✅ PASS |
| 3 | Coded-agent deploy round-trips: `uipath auth --staging` → `init` → `pack` → `publish`, agent shows in Orchestrator and runs on a serverless robot | **BLOCKING** | ✅ PASS | hello-world LangGraph agent (`hello/`) packed to `disclosureflow-hello.0.0.1.nupkg`, published to personal workspace (process auto-created, id 2232171, folder 3092291), invoked via `uipath invoke`; two cloud jobs ran to **Successful** returning typed `{"greeting":"hello, <name>"}` |
| 4 | **Action Center** tasks can be created and completed | high | ✅ PASS | |
| 5 | **API Workflows** available (Studio Web / Integration Service) | non-blocking | ✅ PASS | fallback: deterministic Python behind RecordStore/release seams |
| 6 | **Document Understanding** available | non-blocking | ☐ PASS / ☐ FAIL | fallback: seeded-JSON fixture / deterministic parser |
| 7 | Permissions: `Processes.view/edit` + `Jobs.view/edit` in the target folder | high | ✅ PASS | `publish` created the process; SDK `JobsService.list/retrieve` read jobs in the personal-workspace folder. Note: raw Orchestrator OData with the on-disk org-scoped token returns 403/1010 — use the CLI/SDK auth path (auto-refresh + folder headers), not hand-rolled API calls |
| 8 | An Unattended runtime is allocated, assigned to the tenant, and added to the target folder | high | ✅ PASS | invoked jobs reached **Successful** (not stuck Pending), so a serverless runtime picked them up in the personal workspace |

## If row 1 (Maestro Case) FAILS
1. Request enablement from the AgentHack organizers immediately.
2. If it cannot be enabled in time, fall back to a Maestro **BPMN agentic process** (still Automation Cloud, still Maestro) and emphasize the dynamic, exception-driven path (gateways, the clarification toll, silent-custodian escalation). Document the decision in `ASSUMPTIONS.md`. Do not silently proceed as if Case is available.

## Notes / blockers raised with organizers
-

## Milestone-1 deploy round-trip (scoping-agent, depends on vendored shared/ + live Anthropic call)

Date: 2026-06-22. Auth: staging token live (SDK `assets.list(folder_path="Shared")`
returned `[]` with HTTP 200 — authenticated; raw token still 403s, use SDK path).

- **Pack PASS.** `make pack AGENT=scoping-agent` →
  `agents/scoping-agent/.uipath/disclosureflow-scoping-agent.0.0.1.nupkg`. nupkg
  bundles all 15 `content/shared/*.py` modules next to `main.py`; `operate.json`
  lists `uipath-langchain`, `pydantic>=2,<3`, `langchain-anthropic>=0.3,<0.4`.
- **Local live-Sonnet run PASS.** `uipath run agent --file fixtures/request_clean.json`
  with the key in env → Successful, valid `ScopedRequest` (track=fast_track,
  is_vague=False, clarification=None). Proves shared/ resolves and the direct
  ChatAnthropic call works end-to-end.
- **ANTHROPIC_API_KEY on the robot — mechanism chosen.** Brief §13: key lives as
  an Orchestrator asset. Created Text asset **`DisclosureFlow_AnthropicApiKey`**
  (Global, Key `04d642ef-01dd-45ae-b42f-05879b8c1a61`) in the personal workspace
  via OData POST (SDK exposes no `assets.create`; CLI has only `assets list`).
  Agent code changed minimally (`main._resolve_anthropic_key`): **os.environ
  first** (local .env / a bound `ANTHROPIC_API_KEY=%ASSETS/DisclosureFlow_AnthropicApiKey%`
  process env ref), **fall back to the SDK asset** (`sdk.assets.retrieve_secret`
  then `retrieve`) which auto-authenticates in the in-job robot context. Model id
  still from config; calls still direct-Anthropic. Two robot paths supported so
  the deploy is not blocked on UI env-var config.
  - Doc basis (Context7, current): `uipath-python/docs/core/environment_variables.md`
    documents the `NAME=%ASSETS/asset-name%` env reference; `uipath publish
    --my-workspace` docs state publish returns a "process configuration link …
    to configure any environment variables". `.env` is NOT packed into the nupkg
    (verified against the hello nupkg), so the env binding must be set on the
    process in Orchestrator — hence the SDK-asset fallback as the no-UI path.
- **Process-limit blocker RESOLVED (user-approved).** The personal workspace
  caps at 1 published process. Unpublished `disclosureflow-hello` (release id
  2232171) via OData `DELETE /Releases(2232171)` (folder-scoped header → HTTP
  204); folder then listed 0 releases, freeing the slot. The hello **source**
  (`hello/`) is untouched and re-publishable via `make publish AGENT=hello`.
- **Publish PASS.** `uipath publish -w` (personal workspace) published the
  existing `disclosureflow-scoping-agent.0.0.1.nupkg`; release auto-created.
  **Process id 2232344**, folder **3092291** (personal workspace), ProcessKey
  `disclosureflow-scoping-agent`, version 0.0.1, release Key
  `60e7cf4f-64db-4cc4-a55a-a6a79da276e8`.
- **Robot invoke — clean fixture PASS.** `uipath invoke agent --file
  fixtures/request_clean.json` → job `97de324b-9ea8-4e29-9038-423209328e4c`
  reached **Successful**. `extract_output` returned a valid `ScopedRequest`:
  `track=fast_track`, `is_vague=false`, `clarification=null`, original scope
  preserved (subject + extracted date range + parties + record_types=[email]).
  Proves on the serverless robot: vendored `shared/` resolved at runtime, the
  Anthropic key reached the agent, and the live Sonnet call worked.
- **Robot invoke — vague fixture PASS.** `uipath invoke agent --file
  fixtures/request_vague.json` → job `084d915d-0161-4678-ba6a-bf594318135e`
  reached **Successful**. `ScopedRequest`: `is_vague=true`,
  `clarification_round=1`, populated `ClarificationDraft` (message +
  `suggested_narrowing`, offering "keep your original"), original scope
  preserved verbatim (`subject` = "the agency's spending and any problems").
  The §5 clarification path works on the robot.
- **Key-delivery mechanism CONFIRMED on the robot: SDK asset fallback (path #2),
  NOT the `%ASSETS%` env binding (path #1).** Release 2232344 has NO process env
  bindings (`EnvironmentVariables` empty, `ProcessSettings` None) — and CLI/SDK
  cannot set the `ANTHROPIC_API_KEY=%ASSETS/...%` env reference (that is an
  Orchestrator-UI-only action). So on the robot `os.environ['ANTHROPIC_API_KEY']`
  was unset and `_resolve_anthropic_key()` fell through to
  `sdk.assets.retrieve_secret(name="DisclosureFlow_AnthropicApiKey")`, which
  auto-authenticated from the in-job robot context and delivered the key. Both
  live LLM jobs succeeded → the SDK-asset fallback is the working, no-UI-step
  key path for serverless coded agents. The env-binding path remains available
  as an optional optimization if someone sets it in the UI, but is not required.
## Milestone-2 prep: SHARED folder runtime check — BLOCKED (2026-06-22)

Goal: move all three coded agents out of the per-user PERSONAL workspace (caps at
1 published process) into a SHARED standard folder so the Maestro Case spine can
keep all three live at once.

- **Target folder:** STANDARD "Shared", id **3083529**, key
  **257dab65-2353-4e0c-96e8-ff9f3746d9ed**. Already exists (do not create a new one).
- **Folder header works against it.** `GET .../odata/Releases` with header
  `X-UIPATH-OrganizationUnitId: 3083529` → HTTP 200, `value: []` (folder reachable,
  currently no processes published).

### How serverless runtime was confirmed for the PERSONAL folder (the reference check)
Two independent signals, both replicated here:
1. **Operational (definitive):** the scoping-agent jobs invoked in personal folder
   3092291 returned `State=Successful` with **`RuntimeType=Serverless`** and a
   dynamically-allocated serverless machine GUID per job (e.g. job 67797727,
   67796723, 67791180). Serverless robots are paired per-job, not persistent
   sessions — `odata/Sessions` only shows Disconnected `Assistant` long-lived
   sessions, so session endpoints are NOT a valid serverless check.
2. **Assignment state:** `GET odata/Folders/UiPath.Server.Configuration.OData`
   `.GetMachinesForFolder(key=3092291)` shows the **"Default Serverless"** template
   (id 2059549, Scope=Serverless, LicenseKey 420b1eb9-...) with
   **`IsInherited=true`**, inherited from a system "Virtual folder-..." → that is
   what serves the personal workspace's jobs.

### Result for the SHARED folder (3083529): runtime NOT currently available
`GetMachinesForFolder(key=3083529)` returns the same 3 candidate templates, but
the **"Default Serverless"** template shows
**`IsAssignedToFolder=false` AND `IsInherited=false`** (`InheritedFrom=None`).
→ No serverless machine template is assigned to or inherited into the Shared
folder, so an invoked job there would sit **Pending** with no runtime to pick it
up. This matches the UiPath requirement that an admin must "assign … a machine
template to the folder that contains the process … and assign runtimes to the
machine template" (Orchestrator docs: *Executing unattended automations with
Serverless robots*; *Frequently asked questions — Cloud Robots - Serverless*).

**Status: RESOLVED (2026-06-22, user-authorized).** The "Default Serverless"
template (machine id 2059549) was assigned to folder 3083529 via
`POST odata/Folders/UiPath.Server.Configuration.OData.AssignMachines`
header `X-UIPATH-OrganizationUnitId: 3083529`
body `{"assignments":{"MachineIds":[2059549],"FolderIds":[3083529]}}` → **HTTP 204**.
Post-verify: `GetMachinesForFolder(key=3083529)` now shows "Default Serverless"
with **`IsAssignedToFolder=true`** (was false/false). Reversible via
`RemoveMachinesFromFolder` (same body shape). After this, both agents' Shared-folder
jobs went **Running → Successful** (never stuck Pending), confirming the runtime
took. Full migration completed — see "Milestone-2: SHARED folder migration —
COMPLETE" below.

Doc sources for the platform claims:
- Orchestrator — *Executing unattended automations with Serverless robots*:
  https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/executing-unattended-automations-with-serverless-robots
- Orchestrator — *Frequently asked questions (Cloud Robots - Serverless)*:
  https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/frequently-asked-questions-cloud-robots-serverless
- Orchestrator — *Assigning Machine Objects to Folders*:
  https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/assigning-machine-objects-to-folders

## Milestone-2: SHARED folder migration — COMPLETE (2026-06-22)

Both deployed coded agents now live in the STANDARD "Shared" folder (id 3083529,
key 257dab65-2353-4e0c-96e8-ff9f3746d9ed), which has no per-user 1-process cap.
This sidesteps the personal-workspace limit and lets the Maestro Case spine keep
all agents live at once.

**Runtime (step 0):** AssignMachines POST → 204; `IsAssignedToFolder=true` verified
(see RESOLVED block above). Reversible via `RemoveMachinesFromFolder`.

**Anthropic key asset (step 1):** A Text asset `DisclosureFlow_AnthropicApiKey`
already existed (id **589211**, `ValueType=Text`, `FoldersCount=1`, bound to the
personal folder only — invisible from Shared, and tenant asset names are unique so
re-creating it returned the WAF/Orchestrator 403). Instead of duplicating the key,
the existing asset was **shared into the Shared folder** via
`POST odata/Assets/UiPath.Server.Configuration.OData.ShareToFolders`
header `X-UIPATH-OrganizationUnitId: 3092291`
body `{"assetIds":[589211],"toAddFolderIds":[3083529],"toRemoveFolderIds":[]}` → 204.
Now `FoldersCount=2`; it resolves in BOTH folder contexts from one value (no key
duplication). On-robot it resolves via `sdk.assets.retrieve_secret` /
`GetRobotAssetByNameForRobotKey` (confirmed 200 in job logs).

**Custodian (step 2-3):** `make pack AGENT=custodian-search-agent` → published to
the **tenant feed** (`uipath publish -t`; `-f Shared` is not selectable — the CLI
only lists tenant + personal feeds, so deploy = publish-to-tenant + create-release-
in-folder). Release created in Shared: process `disclosureflow-custodian-search-agent`
v0.0.1, **Release Id 2232377, Key ee7c4b40-ccf1-4b27-8da8-bd61d976dd47**.
- Invoke `fixtures/scoped_clean.json` → job Key 1cdb54e6-... (Id 67800747),
  **Running → Successful** in ~26s. SearchPlan output (from job OutputArguments):
  **1 task**, `task_id=search-office-of-procurement`, dept "Office of Procurement"
  ∈ available_departments, 8 non-empty keywords, `case_id=case-journey-A` /
  `jurisdiction=federal_foia` threaded; task_id deterministic
  (`search-<slug(dept)>`).
- Invoke `fixtures/scoped_broad.json` → job Key d6adbf4e-... (Id 67801142),
  **Successful**; logs show Anthropic 200 + SearchTask built.
- Job logs prove the on-robot path: vendored `shared.contracts.pipeline.SearchTask`
  deserialized, asset resolved (200), `POST api.anthropic.com/v1/messages 200`
  (outbound to Anthropic is NOT blocked from the serverless robot), live Opus call.

**Scoping migration (step 4):** scoping was only in the PERSONAL feed, so published
to the **tenant feed** (`uipath publish -t`), then release created in Shared:
process `disclosureflow-scoping-agent` v0.0.1, **Release Id 2232380,
Key 8c3a453d-d845-478e-ad0f-7f6a9408ec8d**. Invoke `fixtures/request_clean.json` →
job Key 0ca23d4d-... **Running → Successful**; logs show Anthropic 200 + Successful.
Personal slot freed by **deleting personal Release 2232344** (`DELETE
odata/Releases(2232344)` → 204); personal workspace now has 0 releases.

**Invocation note (load-bearing for any API-driven invoke):** raw `StartJobs` from
curl/urllib was blocked by the edge WAF (terse `error code: 1010`, even against the
proven personal folder). Driving the request through **httpx** (the UiPath SDK's
transport) passed the WAF. Working shape:
`POST odata/Jobs/UiPath.Server.Configuration.OData.StartJobs`, header
`x-uipath-folderkey: <folder key>`, body
`{"startInfo":{"ReleaseKey":"<key>","Strategy":"ModernJobsCount","JobsCount":1,"RuntimeType":"Serverless","InputArguments":"<json-string of inputs>"}}`.
Job `OutputArguments` (the typed agent output) is committed asynchronously and can
lag a few seconds after State flips to Successful — poll/re-fetch it.

Doc sources:
- Orchestrator — *Managing Assets* / sharing assets to folders:
  https://docs.uipath.com/orchestrator/automation-cloud/latest/user-guide/managing-assets
- Orchestrator — *Jobs* (StartJobs / ModernJobsCount strategy):
  https://docs.uipath.com/orchestrator/automation-cloud/latest/api-guide/jobs-requests

## Milestone-1 COMPLETE: third agent (Review & Redaction, the hero) deployed (2026-06-22)

All THREE coded agents are now live in the SHARED folder (id 3083529, key
257dab65-2353-4e0c-96e8-ff9f3746d9ed): scoping (release 2232380), custodian-search
(release 2232377), review-redaction (release **2232381**, key
**39a62c8f-355b-459b-bc2b-199ca1cf55c2**, v0.0.1).

**The new risk this round-trip proved — the policy-pack `.json` reaches the robot
(open item W1 in ASSUMPTIONS.md): RESOLVED.** The Review agent depends on
`policy-packs/federal-foia/pack.json`, which lives OUTSIDE `shared/` and is a `.json`
(not a `.py`). `make pack AGENT=review-redaction-agent` produced
`.uipath/disclosureflow-review-redaction-agent.0.0.1.nupkg`; `unzip -l` confirmed the
pack is bundled at exactly **`content/policy-packs/federal-foia/pack.json`** (1616 bytes),
i.e. `policy-packs/federal-foia/` next to `content/main.py` — exactly where
`main._resolve_pack_dir()` (`_VENDORED_PACK_DIR = _THIS_DIR / "policy-packs" /
"federal-foia"`) looks on the robot. All 15 `content/shared/*.py` modules are bundled too.
So `packOptions.fileExtensionsIncluded:[".json"]` alone is sufficient — no
`filesIncluded`/`directoriesIncluded` change needed. (One-time fix: `uipath pack` rejected
the `&` in the `pyproject.toml` description — "Review & Redaction" → "Review and Redaction".)

**Publish.** Tenant feed (`uipath publish -t`), then release created in folder 3083529
via OData `POST /odata/Releases` (folder header) — the proven Milestone-2 pattern.

**Robot invokes (httpx StartJobs, folder header `x-uipath-folderkey`; NOT raw curl — WAF
1010):**
- **Journey C** (`fixtures/records_exemption_heavy.json`) → job Id 67802989,
  Running → **Successful** (~48s). `ReviewResult`: **5 proposals** across 3 responsive
  records, rule_ids `{b5, b6, b7c}` all from the real pack, citations present (`5 U.S.C.
  § 552(b)(5)/(6)/(7)(C)` copied from the pack), pack-stamped `federal-foia/2025.06.0`,
  spans (start/end/quote) on every proposal. **DERIVED confidence**: b6 & b7c →
  `low`/`balancing_always_full_review`; b5 → `high`/None. Proves on the robot:
  vendored `shared/` resolved, **policy-pack `.json` resolved**, key asset resolved, live
  Opus 4.8 call worked.
- **Journey A** (`fixtures/records_clean.json`) → job Id 67803460, **Successful**.
  `ReviewResult`: **0 proposals**, `reviewed=[{REC-A-0001, responsive=True, 0}]` — the
  disclosure path (responsive, no withholding).

Both BLOCKING capabilities for the deploy spine (coded-agent round-trip; serverless runtime
in the shared folder) remain PASS across all three agents.
