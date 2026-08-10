# Pending academic data

Files in this directory are reviewable import packages. They are deliberately
not loaded by application startup and must not be added to
`bundled_scheduler_offering_updates()` or overwrite
`app/data/curriculum_requirements.json` during preparation.

The manual importer defaults to dry-run. Every run must provide the reviewed
file SHA-256 and exact control totals; adding `--apply` is the separate,
intentional deployment step. The commands below contain the controls for the
snapshots captured on 2026-08-09. Do not update the hashes or counts without
reviewing a newly generated package.

## 2026-27 Fall scheduler (2610)

The current snapshot was generated from all 29 subjects advertised by the
official HKUST-GZ Class Schedule & Quota page. It contains 383 offered courses,
801 sections, and 824 canonical weekly meetings. The source HTML hashes and
retrieval timestamp are embedded in the JSON. Date-specific schedule rows and
three A/B-lab grouping approximations are retained as explicit provenance.

To reproduce the package from freshly fetched official pages (this only writes
pending JSON and never connects to a database):

```bash
python -m app.scripts.fetch_hkustgz_wcq \
  --output /tmp/hkustgz-wcq-2610-candidate.json
```

Review and dry-run the captured package:

```bash
python -m app.scripts.import_pending_academic_data scheduler \
  --file app/data/pending/scheduler_offerings/26-27fall.json \
  --expected-sha256 64cf81e1cabe6bef350b6be1c29206329fe22bfe7e6d820eedd812c419d347cc \
  --expected-courses 383 \
  --expected-offered-courses 383 \
  --expected-sections 801 \
  --expected-lectures 824
```

## 2026 cohort curriculum requirements

The curriculum package contains the eight degree majors selected by the live
2026/27 undergraduate catalog. AI extended-major and AI minor records are not
degree majors and are intentionally excluded. The package contains eight
program/cohort rows, 32 requirement groups, and 292 unique referenced course
codes. Every program records its catalog URL, requirement-document URL, source
PDF SHA-256, applicability notes, and reviewed credit ranges.

Review and dry-run the package:

```bash
python -m app.scripts.import_pending_academic_data curriculum \
  --file app/data/pending/curriculum_requirements_2026.json \
  --expected-sha256 a99cbe5c120ba5fcd707651f4609a6ca08e6d5bfa205734979ad6d9739f6b056 \
  --expected-program-definitions 8 \
  --expected-program-cohorts 8 \
  --expected-requirement-groups 32 \
  --expected-unique-course-codes 292
```

The curriculum package requires at least one official HTTPS
`hkust-gz.edu.cn` source URL per program definition. Its dry-run also blocks if
the package would remove an existing requirement group.

Neither command above deploys data. A future operator must first review the
dry-run against the intended database and then deliberately add `--apply` to
the same reviewed command. These pending files are not wired into application
startup or `bundled_scheduler_offering_updates()`.
