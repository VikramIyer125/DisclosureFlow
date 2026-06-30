// ─────────────────────────────────────────────────────────────────────────────
// Journey-B record-query — UiPath Studio Web API Workflow "Script" activity body
// ─────────────────────────────────────────────────────────────────────────────
// Deterministic, seeded reimplementation of steps/record_query_step.py +
// demo-data/journeys.json (journey "B") for the platform. Returns one fan-out
// result per department so the Maestro case can branch on custodian silence.
//
// Journey-B behaviour (from journeys.json):
//   Office of Procurement     -> responded  (returns REC-B-0001)
//   Office of the CIO         -> slow        (returns REC-B-0002 after reminder)
//   Office of Communications  -> silent      (no response -> escalation beat)
//
// Inputs (bind from Stage-1 scoping in the case model):
//   $input.case_id       -> stamped onto every record (use the CaseId system var)
//   $input.jurisdiction  -> defaults to "federal_foia"
//
// Output object (mirror Journey A's record-query return idiom — `return {...}`,
// read downstream via $context.outputs.<ScriptName>):
//   records           : CandidateRecord[]  -> flattened RESPONSIVE records; feeds
//                        Review (SAME field name Journey A's review stage reads)
//   statuses          : {department,status}[]  -> full fan-out, for the timeline
//   silent_department : string  -> the silent dept ("" when none); the case
//                        branches on THIS scalar (no array iteration needed)
//   has_silent        : bool
//
// content_hash values are the sha256 of the byte-exact seed files (incl. trailing
// newline), so they reproduce demo-data/journey-B/*. text is what Review reads;
// both records are clean (no exemption material) -> Review yields 0 proposals.
// ─────────────────────────────────────────────────────────────────────────────

var caseId = $input.case_id;
var jurisdiction = $input.jurisdiction || "federal_foia";

var recProcurement = {
  case_id: caseId,
  jurisdiction: jurisdiction,
  record_ref: "REC-B-0001",
  department: "Office of Procurement",
  record_type: "email",
  task_id: "search-office-of-procurement",
  content_hash: "26620e50bfa37ce88930125e493b0dd9c8fb7fe06a4d03757dfbd36ecae6d29d",
  is_responsive: null,
  text: "From: procurement@agency.gov\nTo: vendor@modernco.example\nSubject: IT modernization program - contract award 2023\n\nThis confirms the award of contract IT-MOD-2023-004 under the agency's IT modernization program. The competitively awarded value is $1,250,000 for cloud-migration services performed during calendar year 2023. Solicitation SOL-2023-IT-11 governed the procurement. No personal or pre-decisional information is contained in this record; it is releasable in full.\n",
  uri: "drive://office-of-procurement/REC-B-0001"
};

var recCio = {
  case_id: caseId,
  jurisdiction: jurisdiction,
  record_ref: "REC-B-0002",
  department: "Office of the CIO",
  record_type: "report",
  task_id: "search-office-of-the-cio",
  content_hash: "e0b14090ff9853c064c7b2c14a1dd32e42f5fe74e756e45cb9f86b0e24de361c",
  is_responsive: null,
  text: "STATUS REPORT - IT MODERNIZATION PROGRAM (CY2023)\nFrom: CIO Program Office\nTo: agency leadership\n\nThe IT modernization program completed its 2023 cloud-migration milestones on schedule. Total appropriated program budget for 2023 was $2.1 million, a matter of public record. This status report contains only factual program information about the modernization effort and contains no pre-decisional deliberation or personal data.\n",
  uri: "drive://office-of-the-cio/REC-B-0002"
};

return {
  // RESPONSIVE records only (responded + slow-that-returned) -> Review input.
  records: [recProcurement, recCio],
  // Full custodian fan-out, for the case timeline / demo narration.
  statuses: [
    { department: "Office of Procurement", status: "responded" },
    { department: "Office of the CIO", status: "slow" },
    { department: "Office of Communications", status: "silent" }
  ],
  // Scalar branch discriminator — the case routes the escalation beat on this.
  silent_department: "Office of Communications",
  has_silent: true
};
