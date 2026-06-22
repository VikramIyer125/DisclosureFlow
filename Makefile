# DisclosureFlow — agent build entry point (vendor-then-pack).
#
# Each coded agent under agents/<AGENT>/ packages INDEPENDENTLY for UiPath. The
# canonical shared/ backbone lives at repo root; an agent must VENDOR a build-time
# copy of shared/ into its own dir before `uipath pack`, because the .nupkg only
# bundles the agent project directory and a uv path-dep / workspace pointing at
# ../shared does not survive on the serverless robot (see ASSUMPTIONS.md). This
# Makefile is the entry point so the vendoring rsync can never be skipped — a bare
# `uipath pack` would silently omit shared/ and ImportError at runtime.
#
# Usage:
#   make vendor  AGENT=scoping-agent   # copy shared/ into the agent dir
#   make init    AGENT=scoping-agent   # vendor + `uipath init` (regenerate schema)
#   make pack    AGENT=scoping-agent   # vendor + init + `uipath pack`
#   make publish AGENT=scoping-agent   # vendor + init + pack + `uipath publish`
#   make clean-vendor AGENT=scoping-agent

AGENT ?=
AGENT_DIR := agents/$(AGENT)

# Copy the canonical shared/ into the agent dir. --delete keeps the vendored copy
# an exact mirror; exclude caches and shared/pyproject.toml (only the .py modules
# ship — the agent's own pyproject declares the deps the robot installs).
RSYNC := rsync -a --delete \
	--exclude='__pycache__' \
	--exclude='*.pyc' \
	--exclude='pyproject.toml' \
	shared/ $(AGENT_DIR)/shared/

# Agents that ALSO need the policy-packs/ data vendored in (the Review & Redaction
# agent is the first/only seam consumer of the federal-foia pack JSON). The pack
# lives OUTSIDE shared/ and is a .json (not a .py), so it must be copied separately
# into the agent dir AND bundled by `uipath pack` via packOptions.fileExtensions-
# Included (".json"). The vendored policy-packs/ is gitignored in the agent dir.
PACK_DATA_AGENTS := review-redaction-agent
RSYNC_POLICY := rsync -a --delete \
	--exclude='__pycache__' \
	--exclude='*.pyc' \
	policy-packs/ $(AGENT_DIR)/policy-packs/

.PHONY: _check vendor init pack publish clean-vendor

_check:
	@if [ -z "$(AGENT)" ]; then \
		echo "ERROR: set AGENT=<name>, e.g. make pack AGENT=scoping-agent"; exit 1; fi
	@if [ ! -d "$(AGENT_DIR)" ]; then \
		echo "ERROR: $(AGENT_DIR) does not exist"; exit 1; fi

vendor: _check
	@echo ">> Vendoring shared/ into $(AGENT_DIR)/shared/"
	@$(RSYNC)
	@if echo " $(PACK_DATA_AGENTS) " | grep -q " $(AGENT) "; then \
		echo ">> Vendoring policy-packs/ into $(AGENT_DIR)/policy-packs/"; \
		$(RSYNC_POLICY); \
	fi

init: vendor
	@echo ">> uipath init in $(AGENT_DIR)"
	@# --no-agents-md-override: regenerate the schema (uipath.json / entry-points.json)
	@# WITHOUT clobbering the agent's authored AGENTS.md and .agent/* docs.
	@cd $(AGENT_DIR) && uv run uipath init --no-agents-md-override

pack: init
	@echo ">> uipath pack in $(AGENT_DIR)"
	@cd $(AGENT_DIR) && uv run uipath pack

publish: pack
	@echo ">> uipath publish in $(AGENT_DIR)"
	@cd $(AGENT_DIR) && uv run uipath publish

clean-vendor: _check
	@echo ">> Removing vendored shared/ from $(AGENT_DIR)"
	@rm -rf $(AGENT_DIR)/shared
	@echo ">> Removing vendored policy-packs/ from $(AGENT_DIR)"
	@rm -rf $(AGENT_DIR)/policy-packs
