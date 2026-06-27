"""RecordStore seam — N department repositories with controllable behavior (brief §7).

The realism that matters is the fan-out + the controllable per-department
response, not the storage medium. Each department has a demo behavior config —
respond | slow | silent | wrong_docs — that drives the §2 stage-3 exception
branches (reminder → escalation) the Maestro case model routes.

`query` returns a `QueryResult` carrying the `status` and the candidate records;
each `CandidateRecord.content_hash` (sha256 of source bytes) starts the §8.4
integrity chain. `jurisdiction` is a real parameter from day one.

Backings:
- `LocalFolderRecordStore` — demo/dev: folders-per-department on local disk,
  with a per-department behavior config. Runnable.
- `DriveRecordStore`       — demo (Google Drive folders) stub.
- `ConnectorRecordStore`   — production connector stub.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts import CandidateRecord, QueryResult, QueryStatus, SearchTerms


@runtime_checkable
class RecordStore(Protocol):
    """Custodian repository fan-out behind one interface (§7)."""

    def list_departments(self, jurisdiction: str) -> list[str]:
        """Department repositories available under `jurisdiction`."""
        ...

    def query(
        self, jurisdiction: str, department: str, terms: SearchTerms, task_id: str
    ) -> QueryResult:
        """Query one department; returns its controllable status + candidate records.

        `task_id` correlates the result to the originating `SearchTask` and feeds
        the §8.5 idempotency key for this query side effect.
        """
        ...


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Default per-department demo behavior for the local backing. department -> status.
# Logged in ASSUMPTIONS.md. Three departments give a fan-out with one happy path,
# one silent (escalation), and one slow (reminder) by default; callers override.
# NOTE: these generic names (hr/legal/field_office) are a bare fallback ONLY.
# The real demo journeys use the canonical "Office of ..." department names and
# their own behavior maps from demo-data/journeys.json, which OVERRIDE this dict.
# Do not seed available_departments from this — use journeys.json's names.
_DEFAULT_BEHAVIOR: dict[str, QueryStatus] = {
    "hr": "responded",
    "legal": "slow",
    "field_office": "silent",
}


class LocalFolderRecordStore:
    """Demo/dev RecordStore over folders-per-department on local disk (§7).

    Layout: ``<root>/<department>/<file>`` — each file is one record. The
    per-department `behavior` config controls the response:
      - 'responded'  : returns matching records;
      - 'slow'       : returns matching records but flags status='slow' so the
                       case model can model a reminder (content still arrives);
      - 'silent'     : returns no records and status='silent' (→ escalation);
      - 'wrong_docs' : returns records that do NOT match the terms (off-scope),
                       status='wrong_docs' (Review marks them non-responsive).

    Keyword matching is a simple case-insensitive substring scan over file text —
    enough for the demo; the medium/matching is deliberately not the realism that
    matters here (§7).
    """

    def __init__(
        self,
        root: Path | str,
        behavior: dict[str, QueryStatus] | None = None,
        case_id: str = "demo-case",
        jurisdiction: str | None = None,
    ) -> None:
        """Args:
        root: Folder containing one subfolder per department.
        behavior: department -> demo status; falls back to `_DEFAULT_BEHAVIOR`.
        case_id: The locked `query` signature (§7) does not pass `case_id`, but
            `QueryResult`/`CandidateRecord` carry `IdentityEnvelope`. In the demo
            the store is wired per-case, so the case identity is constructor
            state. A real store would be told the case by its caller. Defaults to
            'demo-case'.
        jurisdiction: optional default; the per-call `jurisdiction` arg still wins.
        """
        self._root = Path(root)
        self._behavior = dict(_DEFAULT_BEHAVIOR if behavior is None else behavior)
        self._case_id = case_id
        self._default_jurisdiction = jurisdiction

    def list_departments(self, jurisdiction: str) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())

    def _read_records(self, department: str) -> list[tuple[str, str, bytes]]:
        """Return (record_ref, filename, raw_bytes) for every file in a dept folder."""
        dept_dir = self._root / department
        out: list[tuple[str, str, bytes]] = []
        if not dept_dir.is_dir():
            return out
        for f in sorted(dept_dir.iterdir()):
            if f.is_file():
                out.append((f"{department}/{f.name}", f.name, f.read_bytes()))
        return out

    @staticmethod
    def _matches(raw: bytes, terms: SearchTerms) -> bool:
        if not terms.keywords:
            return True
        try:
            text = raw.decode("utf-8", errors="ignore").lower()
        except Exception:
            return False
        return any(kw.lower() in text for kw in terms.keywords)

    @staticmethod
    def _record_type_for(filename: str) -> str:
        suffix = Path(filename).suffix.lower().lstrip(".")
        return suffix or "document"

    def query(
        self, jurisdiction: str, department: str, terms: SearchTerms, task_id: str
    ) -> QueryResult:
        status: QueryStatus = self._behavior.get(department, "responded")
        all_records = self._read_records(department)

        if status == "silent":
            return QueryResult(
                case_id=self._case_id,
                jurisdiction=jurisdiction,
                task_id=task_id,
                department=department,
                status="silent",
                records=[],
            )

        if status == "wrong_docs":
            # Return records that do NOT match the terms (off-scope), so Review
            # exercises the responsiveness check and marks them non-responsive.
            selected = [r for r in all_records if not self._matches(r[2], terms)]
        else:
            selected = [r for r in all_records if self._matches(r[2], terms)]

        records = [
            CandidateRecord(
                case_id=self._case_id,
                jurisdiction=jurisdiction,
                record_ref=record_ref,
                department=department,
                record_type=self._record_type_for(filename),
                task_id=task_id,
                content_hash=_sha256_bytes(raw),
                is_responsive=None,
                text=raw.decode("utf-8", errors="ignore"),
                uri=str(self._root / record_ref),
            )
            for (record_ref, filename, raw) in selected
        ]
        return QueryResult(
            case_id=self._case_id,
            jurisdiction=jurisdiction,
            task_id=task_id,
            department=department,
            status=status,
            records=records,
        )


class DriveRecordStore:
    """Demo RecordStore over Google Drive folders-per-department (stub).

    Not implemented: wiring real Drive credentials is out of MVP scope (§1, §13).
    `LocalFolderRecordStore` is the runnable demo/dev backing; this is the named
    Drive variant to swap in later.
    """

    def list_departments(self, jurisdiction: str) -> list[str]:
        raise NotImplementedError(
            "DriveRecordStore is a Drive-backed stub; use LocalFolderRecordStore for the demo."
        )

    def query(
        self, jurisdiction: str, department: str, terms: SearchTerms, task_id: str
    ) -> QueryResult:
        raise NotImplementedError(
            "DriveRecordStore is a Drive-backed stub; use LocalFolderRecordStore for the demo."
        )


class ConnectorRecordStore:
    """Production RecordStore over an enterprise records connector (stub).

    Not implemented: production integration is out of MVP scope (§1).
    """

    def list_departments(self, jurisdiction: str) -> list[str]:
        raise NotImplementedError(
            "ConnectorRecordStore is a production stub; use LocalFolderRecordStore for the demo."
        )

    def query(
        self, jurisdiction: str, department: str, terms: SearchTerms, task_id: str
    ) -> QueryResult:
        raise NotImplementedError(
            "ConnectorRecordStore is a production stub; use LocalFolderRecordStore for the demo."
        )


__all__ = [
    "RecordStore",
    "LocalFolderRecordStore",
    "DriveRecordStore",
    "ConnectorRecordStore",
]
