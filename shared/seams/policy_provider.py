"""PolicyProvider seam — deterministic legal rules, NOT a vector store (brief §7).

Returns the closed set of FOIA exemption `Rule`s an agent may ground a
withholding in, so agents cannot invent exemptions or citations (§8.3). The
agent reasons over WHATEVER comes back — it never hardcodes rule ids or counts,
so adding a new exemption is a pack edit (extensibility, §7).

`jurisdiction` is a real parameter on every method from day one even though the
only value is 'federal_foia'.

Backings:
- `FederalFoiaPackProvider` — demo: loads ``policy-packs/federal-foia/`` JSON.
- `RegistryPolicyProvider` — production stub (raises NotImplementedError).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from ..contracts import FEDERAL_FOIA, PackMetadata, Rule


@runtime_checkable
class PolicyProvider(Protocol):
    """Deterministic source of FOIA exemption rules (§7, §8.3)."""

    def get_applicable_rules(self, jurisdiction: str, record_type: str) -> list[Rule]:
        """All rules that may apply to `record_type` under `jurisdiction`.

        The §8.3 validator treats the ids of these returned rules as the closed
        set a proposal's `rule_id` must belong to.
        """
        ...

    def get_rule(self, jurisdiction: str, rule_id: str) -> Optional[Rule]:
        """Resolve a single rule by id, or None if it is not in the pack."""
        ...

    def pack_metadata(self, jurisdiction: str) -> PackMetadata:
        """Versioning metadata (pack_id, version, effective_date) for the pack."""
        ...


# Default location of the demo pack, relative to the repo root. Resolved from
# this file's location so it works regardless of the process CWD.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PACK_DIR = _REPO_ROOT / "policy-packs" / "federal-foia"


class FederalFoiaPackProvider:
    """Demo PolicyProvider backing the 'federal-foia' pack (b5 / b6 / b7c).

    Loads the versioned pack JSON once and serves rules from it. The MVP pack
    declares every rule's `required_test_elements`, so the §8.3 completeness
    check is fully data-driven. In the MVP every exemption is potentially
    applicable to every record_type, so `get_applicable_rules` returns the full
    set; a real pack could filter by record_type without changing the seam.
    """

    def __init__(self, pack_dir: Path | str = _DEFAULT_PACK_DIR) -> None:
        self._pack_dir = Path(pack_dir)
        self._metadata, self._rules = self._load(self._pack_dir)
        self._rules_by_id = {r.id: r for r in self._rules}

    @staticmethod
    def _load(pack_dir: Path) -> tuple[PackMetadata, list[Rule]]:
        pack_file = pack_dir / "pack.json"
        if not pack_file.is_file():
            raise FileNotFoundError(f"federal-foia pack not found at {pack_file}")
        data = json.loads(pack_file.read_text(encoding="utf-8"))
        metadata = PackMetadata(
            pack_id=data["pack_id"],
            version=data["version"],
            effective_date=data["effective_date"],
            jurisdiction=data["jurisdiction"],
        )
        rules = [Rule.model_validate(r) for r in data["rules"]]
        return metadata, rules

    def _check_jurisdiction(self, jurisdiction: str) -> None:
        if jurisdiction != self._metadata.jurisdiction:
            raise ValueError(
                f"FederalFoiaPackProvider serves {self._metadata.jurisdiction!r}, "
                f"got jurisdiction={jurisdiction!r}"
            )

    def get_applicable_rules(self, jurisdiction: str, record_type: str) -> list[Rule]:
        self._check_jurisdiction(jurisdiction)
        # MVP: every exemption is potentially applicable to any record_type; the
        # agent decides which actually applies. `record_type` is accepted so a
        # future pack can filter without a seam change.
        return list(self._rules)

    def get_rule(self, jurisdiction: str, rule_id: str) -> Optional[Rule]:
        self._check_jurisdiction(jurisdiction)
        return self._rules_by_id.get(rule_id)

    def pack_metadata(self, jurisdiction: str) -> PackMetadata:
        self._check_jurisdiction(jurisdiction)
        return self._metadata


class RegistryPolicyProvider:
    """Production PolicyProvider stub (multi-jurisdiction rule registry).

    Intentionally not implemented: production policy integration is out of MVP
    scope (§1). Present so the seam has a named prod backing to swap in.
    """

    def get_applicable_rules(self, jurisdiction: str, record_type: str) -> list[Rule]:
        raise NotImplementedError(
            "RegistryPolicyProvider is a production stub; use FederalFoiaPackProvider for the demo."
        )

    def get_rule(self, jurisdiction: str, rule_id: str) -> Optional[Rule]:
        raise NotImplementedError(
            "RegistryPolicyProvider is a production stub; use FederalFoiaPackProvider for the demo."
        )

    def pack_metadata(self, jurisdiction: str) -> PackMetadata:
        raise NotImplementedError(
            "RegistryPolicyProvider is a production stub; use FederalFoiaPackProvider for the demo."
        )


__all__ = ["PolicyProvider", "FederalFoiaPackProvider", "RegistryPolicyProvider", "FEDERAL_FOIA"]
