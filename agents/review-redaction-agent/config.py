"""Per-step model configuration for the Review & Redaction agent (brief §9, §13).

Model is a *per-step config value*, never hardcoded in business logic. Brief §9
maps the Review & Redaction reasoning step (responsiveness + exemption
classification + foreseeable-harm rationale) to Opus 4.8.

This thin Milestone-1 build runs a single combined review node on Opus 4.8, but
the lookup is structured exactly like the scoping / custodian agents' so a future
split (e.g. a lighter Haiku step for responsiveness triage, or the Milestone-5
self-consistency sampling step which would re-run the SAME `review` model 3-5×)
is a config change, not a refactor: each step name resolves independently through
``model_for`` and its own ``<STEP>_MODEL`` env override.

If a configured model is unavailable on the chosen provider, the caller falls
back to ``DEFAULT_MODEL`` and notes it in ASSUMPTIONS.md (brief §8 / build-prompt).
"""

from __future__ import annotations

import os
from typing import Optional

# Default per brief §9 (Review & Redaction → Opus 4.8). Kept here as a named
# constant so business logic never embeds a model id; the env override makes
# every step independently tunable.
DEFAULT_MODEL = "claude-opus-4-8"

_STEP_DEFAULTS: dict[str, str] = {
    # Heavy reasoning: responsiveness + exemption classification + foreseeable-harm
    # rationale + the filled, data-driven exemption test. Self-consistency sampling
    # (Milestone-5 stretch) re-runs THIS same step, so it shares the `review` key.
    "review": "claude-opus-4-8",
}

_ENV_KEY = {
    "review": "REVIEW_MODEL",
}


def model_for(step: str) -> str:
    """Resolve the model id for a build step.

    Order: explicit ``<STEP>_MODEL`` env var → the brief §9 default for that
    step → ``DEFAULT_MODEL``. Returns a model id string; the LLM client decides
    availability. Never hardcode the result in calling code — always go through
    this helper so the per-step mapping stays a config concern.
    """
    env_var = _ENV_KEY.get(step)
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return override
    return _STEP_DEFAULTS.get(step, DEFAULT_MODEL)


def temperature_for(step: str) -> Optional[float]:
    """Resolve the sampling temperature for a step, or ``None`` to omit it.

    Opus 4.8 (the §9 model for this agent) REJECTS the ``temperature`` parameter
    ("`temperature` is deprecated for this model"; confirmed live on the
    Custodian/Search agent, also Opus 4.8), so by default this returns ``None``
    and the caller omits ``temperature`` from the client entirely. A temperature
    is only sent when `REVIEW_TEMPERATURE` is explicitly set — e.g. if a step is
    reconfigured onto a model that still accepts it. This keeps the model id a
    config concern without baking a per-model quirk into logic.
    """
    raw = os.environ.get("REVIEW_TEMPERATURE")
    if raw is None or raw == "":
        return None
    return float(raw)
