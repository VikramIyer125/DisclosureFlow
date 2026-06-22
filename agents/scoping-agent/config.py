"""Per-step model configuration for the Intake/Scoping agent (brief §9, §13).

Model is a *per-step config value*, never hardcoded in business logic. Brief §9
maps the heavy interpret/scope step to Sonnet 4.6 and the lighter sub-steps
(triage/track classification, clarification message drafting, field
extraction/normalization) to Haiku 4.5.

This thin Milestone-1 build runs a single combined scoping node on Sonnet 4.6,
but the lookup is structured so a future split to per-step models (Haiku for the
light steps) is a config change, not a refactor: each step name resolves
independently through ``model_for`` and its own ``<STEP>_MODEL`` env override.

If a configured model is unavailable on the chosen provider, the caller falls
back to ``DEFAULT_MODEL`` and notes it in ASSUMPTIONS.md (brief §8 / build-prompt).
"""

from __future__ import annotations

import os

# Defaults per brief §9. Kept here as named constants so business logic never
# embeds a model id; the env override makes every step independently tunable.
DEFAULT_MODEL = "claude-sonnet-4-6"

_STEP_DEFAULTS: dict[str, str] = {
    # Heavy reasoning: interpret the request, detect vagueness, propose scope.
    "scope": "claude-sonnet-4-6",
    # Light steps (nominally Haiku 4.5 per §9). In the thin build these are
    # folded into the single scope node; the entries exist so a later split is a
    # config edit. They are not invoked separately yet.
    "track": "claude-haiku-4-5-20251001",
    "clarification": "claude-haiku-4-5-20251001",
    "extract": "claude-haiku-4-5-20251001",
}

_ENV_KEY = {
    "scope": "INTAKE_MODEL",
    "track": "TRACK_MODEL",
    "clarification": "CLARIFICATION_MODEL",
    "extract": "EXTRACT_MODEL",
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


def temperature_for(step: str) -> float:
    """Scoping is a structured-extraction task; keep it deterministic."""
    return float(os.environ.get("INTAKE_TEMPERATURE", "0"))
