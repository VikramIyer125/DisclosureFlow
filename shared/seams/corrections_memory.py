"""CorrectionsMemory seam — advisory officer-correction memory (brief §7, §8).

ADVISORY ONLY, never authoritative: a stored correction informs a future
proposal but can never set `rule_id` or confidence or bypass the PolicyProvider
or the human gate. Retrieved corrections surface into human review (§5). Nothing
in this seam may write a rule_id or a confidence onto a proposal — it only
returns past `Correction`s for the Review agent to consider.

`add` is append-only and idempotent on the derived key
``jurisdiction:record_ref:rule_id:pack_version:officer:round`` (§8.5) so a
replay does not double-log. `jurisdiction` is a real parameter from day one.

Backings:
- `AppendOnlyCorrectionLog`     — demo/MVP: append-only JSONL; retrieve does
  recency + exact-match on record_type + rule_id. `embedding` stays None.
- `LlamaIndexCorrectionsMemory` — stretch stub (raises NotImplementedError).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts import Correction, RecordContext


def _seg(value: str) -> str:
    """Escape a key segment so an embedded ':' can't shift the field boundary.

    The key joins segments with ':'. A raw value containing ':' (e.g. a
    record_ref or officer id) would otherwise be ambiguous — ``a:b`` + ``c``
    and ``a`` + ``b:c`` would collide. We backslash-escape '\\' then ':' inside
    each segment, making the join unambiguous (auditor S2).
    """
    return str(value).replace("\\", "\\\\").replace(":", "\\:")


def correction_key(c: Correction, clarification_round: int | None = None) -> str:
    """Derived idempotency key for a correction (§8.5).

    Keyed on the grounding identity that makes a correction unique within the
    cross-case advisory log, in this stable field order:
    ``jurisdiction:record_ref:rule_id:pack_version:officer:round``. `round`
    defaults to 0 when the caller has no review-round context. The store dedupes
    appends on this key.

    `case_id` is intentionally absent: corrections are cross-case advisory
    memory (brief §7/§1), retrieved by RecordContext rather than by case, so a
    correction must remain identifiable independent of which case produced it.
    Adding `jurisdiction` and `pack_version` keeps two genuinely distinct
    corrections from colliding when they share record_ref/rule_id/officer/round.

    Each segment is escaped via `_seg` so a value containing ':' cannot create
    an ambiguous key (auditor S2).
    """
    rnd = 0 if clarification_round is None else clarification_round
    parts = [
        _seg(c.jurisdiction),
        _seg(c.span.record_ref),
        _seg(c.rule_id),
        _seg(c.pack_version),
        _seg(c.officer),
        _seg(str(rnd)),
    ]
    return ":".join(parts)


@runtime_checkable
class CorrectionsMemory(Protocol):
    """Advisory store of grounded officer corrections (§7)."""

    def add(self, correction: Correction) -> None:
        """Append a correction; idempotent on its derived key (§8.5)."""
        ...

    def retrieve(self, jurisdiction: str, ctx: RecordContext, k: int) -> list[Correction]:
        """Return up to `k` advisory corrections relevant to `ctx`."""
        ...


class AppendOnlyCorrectionLog:
    """Demo/MVP CorrectionsMemory: append-only JSONL log (§7).

    Storage is a single JSONL file (one serialized `Correction` per line) chosen
    over SQLite for the MVP: it is trivially append-only, human-readable for the
    audit story, and needs no schema/driver. Retrieval is recency-ordered exact
    matching on `record_type` + `rule_id`; `embedding` stays None until the
    LlamaIndex stretch backing.

    Append is idempotent on `correction_key`: if a line with the same derived key
    already exists, the add is a no-op (check-then-act, §8.5).
    """

    def __init__(self, log_path: Path | str) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._log_path.exists():
            self._log_path.touch()

    def _existing_keys(self) -> set[str]:
        keys: set[str] = set()
        for c in self._read_all():
            keys.add(correction_key(c))
        return keys

    def _read_all(self) -> list[Correction]:
        out: list[Correction] = []
        text = self._log_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(Correction.model_validate_json(line))
        return out

    def add(self, correction: Correction) -> None:
        # Advisory-only guard is structural: a Correction has no field that can
        # set a proposal's rule_id/confidence, so simply persisting it cannot
        # bypass the PolicyProvider or human gate.
        key = correction_key(correction)
        if key in self._existing_keys():
            return  # idempotent: already logged (§8.5)
        line = correction.model_dump_json()
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def retrieve(self, jurisdiction: str, ctx: RecordContext, k: int) -> list[Correction]:
        if k <= 0:
            return []
        matches = [
            c
            for c in self._read_all()
            if c.jurisdiction == jurisdiction and c.record_context.record_type == ctx.record_type
        ]
        # Recency: most recent first. The log is append-order, so reverse it.
        matches.sort(key=lambda c: c.timestamp, reverse=True)
        return matches[:k]


class LlamaIndexCorrectionsMemory:
    """Stretch CorrectionsMemory: vector retrieval via LlamaIndex (stub).

    Not implemented (§1, §7: retrieval/vector backing is stretch only). Present
    as the named stretch backing so the seam can swap to it without touching
    callers. It would embed `record_context` and retrieve by similarity, still
    advisory-only.
    """

    def add(self, correction: Correction) -> None:
        raise NotImplementedError(
            "LlamaIndexCorrectionsMemory is a stretch backing; use AppendOnlyCorrectionLog for the MVP."
        )

    def retrieve(self, jurisdiction: str, ctx: RecordContext, k: int) -> list[Correction]:
        raise NotImplementedError(
            "LlamaIndexCorrectionsMemory is a stretch backing; use AppendOnlyCorrectionLog for the MVP."
        )


__all__ = [
    "CorrectionsMemory",
    "AppendOnlyCorrectionLog",
    "LlamaIndexCorrectionsMemory",
    "correction_key",
]
