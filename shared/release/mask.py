"""Canonical redaction-mask convention — ONE source of truth (brief §8.4).

The redaction-application convention (how an approved span is masked in the
released bytes, and therefore what `approved_content_hash` is computed over)
MUST be identical on both sides of the §8.4 integrity chain:

  * the **Review & Redaction agent** computes `ApprovedRedaction.approved_content_hash`
    when the officer approves a span at the HITL gate (`agents/review-redaction-agent`);
  * the **release step** (`steps/release_step.py`) re-applies the approved spans to
    the source bytes and the §8.4 guard re-hashes them — the guard PASSES only if
    the agent's hash equals the release step's recompute.

If the two used different mask characters or different application semantics, the
guard would block every honest release. So the convention lives here, in
`shared/release/` (which is vendored into the agents AND imported by the release
step), and both sides import it. This is a shared *helper*, not a contract change.

Convention (logged in ASSUMPTIONS.md):
  * Each approved/edited span's characters are replaced 1:1 with the FULL BLOCK
    character `█` — NON-REMOVABLE (the original bytes are gone) and
    LENGTH-PRESERVING (later spans' offsets on the same record stay valid).
  * Only `approved`/`edited` decisions are applied; `rejected` content is left
    intact (the disclosure default — a rejected over-redaction RELEASES the text).
  * For an `edited` decision the officer's `edited_span` is applied, not the
    originally proposed span.
  * Spans are clamped to the text bounds and applied right-to-left (robust even
    though length is preserved).
  * The post-redaction text is encoded UTF-8 and sha256'd to form the per-record
    `approved_content_hash`. The hash is RECORD-LEVEL: every `ApprovedRedaction`
    on the same record carries the same hash (all that record's approved spans
    applied together), which is exactly what the release step recomputes.
"""

from __future__ import annotations

import hashlib

# The demo redaction-application marker. FULL BLOCK '█' (U+2588). One source of
# truth for both the agent's approved-hash computation and the release step's
# re-application; changing it here changes both sides together.
REDACTION_CHAR = "█"


def effective_span(
    decision: str, start: int, end: int, edited_start: int | None, edited_end: int | None
) -> tuple[int, int]:
    """The (start, end) actually applied: the officer's edited span if 'edited'.

    Kept primitive (ints, not a contract type) so this helper has no dependency
    on the pipeline contracts and can be imported from anywhere — the agent
    passes the proposal/edited offsets, the release step passes the
    `ApprovedRedaction` span offsets.
    """
    if decision == "edited" and edited_start is not None and edited_end is not None:
        return edited_start, edited_end
    return start, end


def apply_mask(source_text: str, spans: list[tuple[int, int]]) -> str:
    """Replace each span's chars with `REDACTION_CHAR`, length-preserving.

    `spans` are the EFFECTIVE (start, end) ranges to mask (already resolved for
    edits, already filtered to approved/edited). Right-to-left application and
    bounds-clamping match the release step exactly. Returns the redacted text.
    """
    chars = list(source_text)
    n = len(chars)
    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        start = max(0, start)
        end = min(n, end)
        for i in range(start, end):
            chars[i] = REDACTION_CHAR
    return "".join(chars)


def redacted_content_hash(source_text: str, spans: list[tuple[int, int]]) -> str:
    """sha256 of the post-redaction bytes for one record (the §8.4 hash).

    This is the EXACT value the release step computes per record and the §8.4
    guard re-checks. `spans` are the effective approved/edited ranges for THIS
    record. The hash is record-level: pass ALL approved spans for the record.
    """
    redacted = apply_mask(source_text, spans).encode("utf-8")
    return hashlib.sha256(redacted).hexdigest()


__all__ = ["REDACTION_CHAR", "effective_span", "apply_mask", "redacted_content_hash"]
