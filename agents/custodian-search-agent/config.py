"""Per-step model configuration for the Custodian/Search agent (brief §9, §13).

Model is a *per-step config value*, never hardcoded in business logic. Brief §9
maps the Custodian/Search reasoning step (pick departments + generate search
terms) to Opus 4.8.

This thin Milestone-1 build runs a single combined planning node on Opus 4.8,
but the lookup is structured exactly like the scoping agent's so a future split
(e.g. a lighter Haiku step for keyword normalization) is a config change, not a
refactor: each step name resolves independently through ``model_for`` and its own
``<STEP>_MODEL`` env override.

If a configured model is unavailable on the chosen provider, the caller falls
back to ``DEFAULT_MODEL`` and notes it in ASSUMPTIONS.md (brief §8 / build-prompt).
"""

from __future__ import annotations

import os
from typing import Optional

# Default per brief §9 (Custodian/Search → Opus 4.8). Kept here as a named
# constant so business logic never embeds a model id; the env override makes
# every step independently tunable.
DEFAULT_MODEL = "claude-opus-4-8"

_STEP_DEFAULTS: dict[str, str] = {
    # Heavy reasoning: choose departments to task + generate per-department terms.
    "plan": "claude-opus-4-8",
}

_ENV_KEY = {
    "plan": "CUSTODIAN_MODEL",
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
    ("`temperature` is deprecated for this model"), so by default this returns
    ``None`` and the caller omits ``temperature`` from the client entirely. A
    temperature is only sent when `CUSTODIAN_TEMPERATURE` is explicitly set —
    e.g. if a step is reconfigured onto a model that still accepts it. This keeps
    the model id a config concern without baking a per-model quirk into logic.
    """
    raw = os.environ.get("CUSTODIAN_TEMPERATURE")
    if raw is None or raw == "":
        return None
    return float(raw)
