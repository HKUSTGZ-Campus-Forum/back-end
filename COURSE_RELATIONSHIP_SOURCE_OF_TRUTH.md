# Course relationship source of truth and migration plan

Status: implementation complete; production data migration approved by the user on 2026-08-24; execution pending.

## Architecture decision

Course relationships and semester offerings are separate domains:

- SISN/class-quota is authoritative for offerings, sections, meetings, quota, enrolment, and availability.
- HKUST(GZ) Program & Course Catalog (PCC) is authoritative for catalog title, description, prerequisite, corequisite, and exclusion rules.
- Follow-on courses are never separately imported; they are the reverse lookup of current prerequisite edges.
- Course overview and Course Graph consume the same normalized requirement records.

The existing `CourseCatalogVersion`, `CourseCatalogRequirement`, and `CourseRequirementEdge` tables support this design, so no schema migration is required.

The official PCC source is:

`POST https://pcc.hkust-gz.edu.cn/api/bdp/pg-course-catalog?page=1&size=1000`

with `term_code` and `career_type=UG` filters. The importer reads course identity/content, the three relationship fields, and source term/update metadata. Each course payload receives a content-derived source version (`term:hash`), making repeated imports idempotent.

Rule authority is ordered as follows:

1. `sis_course_catalog`
2. bundled `course_catalog.json` / `course_prerequisites.json`
3. legacy course-row fields

Offering sources such as `sisn` and `scheduler_offerings` are excluded from rule authority. The parser preserves raw text and classifies each rule as `course`, `mixed`, `non_course`, or `empty`. Pure course expressions preserve parenthesized AND/OR structure; mixed prose remains visible while only proven course references become edges.

## Runtime contract

- `GET /courses/by-code/<code>/overview` returns normalized requirements, related-course links, provenance, and reverse-derived downstream courses. Compatibility string fields are produced from the same records.
- `GET /courses/relationships/graph` dynamically produces the established graph component/line shape from current rules and includes source metadata.
- The frontend prefers the unified endpoint and temporarily falls back to the static scheduler map during a staggered deployment.
- Course detail displays source/version, clickable related-course chips, and downstream courses.
- Course Graph labels official versus legacy-fallback data.

The CLI defaults to a non-mutating dry-run:

```bash
python -m app.scripts.sync_official_course_catalog --term 2610
```

Mutation requires `--apply`. A guarded six-hour background sync exists but defaults to `COURSE_CATALOG_SYNC_ENABLED=false`; enable it only after the first environment-specific migration is approved and verified.

## Production data migration plan

### Source and target

- Source: PCC public API, term `2610`, career `UG`.
- Target: shared dev first; school production only after explicit approval of this plan and reviewed dev results.
- Target tables:
  - `courses`: upsert official identity and canonical catalog fields.
  - `course_catalog_versions`: append content-addressed official versions.
  - `course_catalog_requirements`: create three normalized records per new/reparsed version.
  - `course_requirement_edges`: create resolved course-to-course references.
- Migration type: insert/update for `courses`; append/insert for relationship tables. No deletes and no schema migration.

### Verified 2026-08-24 scope

- 259 official undergraduate courses.
- 259 catalog versions and 777 normalized requirement records on an empty target.
- 169 non-empty prerequisite, 16 corequisite, and 34 exclusion rules.
- Parser classification: 189 course-only, 22 mixed, 8 non-course, and 558 empty records.
- In-memory apply: 387 resolved reference edges; 3 unresolved references (`UCUG1052A`, `UCUG1052I`, `UCUG1052S`).
- Generated graph: 380 components (259 courses plus logic nodes) and 508 relationship segments.

Regenerate counts against dev/production immediately before apply because existing course rows and previously imported versions change the insert/update split.

### Overwrite behavior

- Posts, reviews, offerings, sections, carts, academic records, curriculum requirements, and legacy scheduler-map tables are not deleted or replaced.
- Existing `courses` title, description, credits, subject, catalog number, and active status may be updated from PCC.
- Legacy rule fields remain for compatibility/fallback, but official normalized requirements take API precedence.
- Existing catalog versions remain. A new version is appended only when a course's official content changes; stale parser output is transactionally rebuilt without duplicating edges.

### Dry-run and validation

1. Save the source response hash and dry-run output.
2. Confirm course count is within the `[150, 500]` guard.
3. Review inserted codes, relation counts, parser-kind counts, and every unresolved reference.
4. Run relationship/parser/overview/graph tests.
5. Apply to dev and rerun; the second run must create zero versions.
6. Compare pure AND/OR, mixed prose, empty rules, suffixed codes, and reverse downstream examples.
7. Validate desktop/mobile, Chinese/English, and light/dark course detail and graph pages on `localhost:3000` against dev.

### Backup and rollback

- Before school-production apply, use the verified backup path in `deploy-release.sh --activate`; never bypass it.
- Record import timestamp, source version, and snapshot SHA-256.
- Application rollback: deploy previous backend/frontend SHAs; compatibility fields and static graph endpoints remain available.
- Data rollback: delete only reviewed `course_catalog_versions` rows with `source='sis_course_catalog'` and the exact source-version/snapshot scope. Cascades remove their requirements and edges. Restore changed `courses` fields from the verified pre-deploy backup if full data rollback is required.
- Keep legacy scheduler-map data until the official graph is successfully observed in production and a separate deletion plan is approved.

Before setting `database_change.approved=true` in the school-production release manifest, obtain explicit approval referencing this plan and the final dev dry-run counts. Enabling `COURSE_CATALOG_SYNC_ENABLED=true` is part of that same product-data approval.
