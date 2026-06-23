# demo-data — seeded department repositories for the three journeys (§11)

Synthetic, obviously-fake record folders that back the demo `LocalFolderRecordStore`
(`shared/seams/record_store.py`). One subtree per journey; one subfolder per
department; one text file per record. **No real PII** — all names, SSNs, phones,
and addresses are invented for the demo.

This tree is deliberately **outside `shared/`** so it is not vendored into every
agent package (the agents only need the `shared/` code, not the demo corpus).
The two seam *step* callables that drive this data live in `steps/`.

## Layout

```
demo-data/
├── journeys.json                 # per-journey root + department→behavior wiring
├── journey-A/  (fast-track)
│   └── Office of Procurement/REC-A-0001.txt
├── journey-B/  (orchestration)
│   ├── Office of Procurement/REC-B-0001.txt   (responded)
│   ├── Office of the CIO/REC-B-0002.txt        (slow)
│   └── Office of Communications/               (silent — empty)
└── journey-C/  (governance)
    ├── Office of Human Resources/REC-C-0001.txt        (b6 PII)
    ├── Office of the CIO/REC-C-0002.txt                 (b5 deliberative)
    └── Office of the Inspector General/REC-C-0003.txt   (b7c law-enforcement)
```

## Department → behavior → journey mapping

| Journey | Department                       | Behavior   | Drives                                   |
|---------|----------------------------------|------------|------------------------------------------|
| A       | Office of Procurement            | responded  | one clean responsive record, 0 exemptions|
| B       | Office of Procurement            | responded  | happy-path fan-out member                |
| B       | Office of the CIO                | slow       | reminder branch                          |
| B       | Office of Communications         | silent     | escalation branch (no records)           |
| C       | Office of Human Resources        | responded  | b6 personal-privacy PII                  |
| C       | Office of the CIO                | responded  | b5 pre-decisional / deliberative         |
| C       | Office of the Inspector General  | responded  | b7c law-enforcement personal info        |

## Hash coherence with the agent fixtures

The Journey-A and Journey-C record files are **byte-identical** to the `text` in
`agents/review-redaction-agent/fixtures/{records_clean,records_exemption_heavy}.json`,
so `sha256(file bytes)` reproduces the `content_hash` recorded in those fixtures.
This keeps the spine coherent: querying the seed data yields the same
`content_hash` (the §8.4 integrity-chain root) the Review fixtures already assume.

The Journey-C record text contains the verbatim spans the Review agent quotes
(e.g. `SSN 123-45-6789`, `Jane Doe`, the pre-decisional recommendation, the IG
witness name), so span location by exact-quote works against this corpus.
