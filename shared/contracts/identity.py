"""Identity envelope shared by every in-flight contract (brief §7, §10).

`IdentityEnvelope` is mixed into every pipeline contract from `ScopedRequest`
onward so that the case identity and jurisdiction ride along the data as it
crosses the Maestro Service Task boundary. This is what lets the §8 invariants
(typed-output validation, release integrity) and a future multi-jurisdiction
PolicyProvider key off real fields rather than out-of-band context.

LOCKED (spec item 2): `jurisdiction` is a plain ``str`` (default ``"federal_foia"``),
never an Enum — multi-jurisdiction is *architected for*, not built. `pack_id` /
`pack_version` are added separately on the proposal-and-after contracts via
`PackStamp`, because those stages are bound to a specific PolicyProvider pack
version for auditability.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

FEDERAL_FOIA = "federal_foia"
"""The only jurisdiction value in the MVP. Pass it as a real parameter from day one."""


class ContractModel(BaseModel):
    """Base config for every DisclosureFlow contract.

    Configured for clean JSON round-trip across the Maestro Service Task
    boundary (``model_validate_json`` / ``model_dump(mode="json")``) and to
    reject unknown fields so a drifting producer fails loudly at the boundary
    rather than silently dropping data.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        ser_json_timedelta="iso8601",
    )


class IdentityEnvelope(ContractModel):
    """Case identity carried by every contract from `ScopedRequest` onward (§7)."""

    case_id: str = Field(strict=True, description="Maestro case instance id; ties every contract to one case.")
    jurisdiction: str = Field(
        default=FEDERAL_FOIA,
        description="Legal regime. Only 'federal_foia' in MVP; kept a str so new regimes are additive.",
    )


class PackStamp(ContractModel):
    """PolicyProvider pack version binding (added from `RedactionProposal` onward).

    Pins the exact rule pack a withholding decision was made against, so the
    audit trail and the §8.3 validator can resolve the *same* rule set later.
    """

    pack_id: str = Field(strict=True, description="PolicyProvider pack id, e.g. 'federal-foia'.")
    pack_version: str = Field(strict=True, description="Pack semantic version the decision was grounded in.")
