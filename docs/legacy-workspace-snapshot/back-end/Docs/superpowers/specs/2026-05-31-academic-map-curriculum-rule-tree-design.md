# Academic Map Curriculum Rule Tree Design

Date: 2026-05-31

## 1. Scope

Rebuild the Academic Map curriculum requirement model for every currently bundled
program and cohort. The source of truth is the official PDF set in
`../更新材料`, not the existing flattened JSON.

The covered sources are:

- `AI 23 24.pdf`
- `AI 25.pdf`
- `Curriculum Requirements - DSBD - 2023 cohort.pdf`
- `DSA 24.pdf`
- `DSA 25.pdf`
- `AMAT Curriculum Updated.pdf`
- `Curriculum Requirement - MSE - 2024 cohort - updated on May 19.pdf`
- `SMMG 23 24.pdf`
- `SMMG 25.pdf`
- `FTEC 25.pdf`
- `ROAS 25.pdf`
- `MICS 25.pdf`
- `SEEN 26.pdf`

This work includes curriculum data, backend synchronization, backend evaluation,
API output, frontend rendering, bilingual copy, and tests. It does not change the
prerequisite expression model in `course_prerequisites.json`; that model already
preserves nested `AND` and `OR` logic.

## 2. Problem

The current curriculum payload stores requirement alternatives as flattened
arrays:

```json
{
  "required_courses": ["UFUG2104"],
  "choices": ["UFUG1102", "UFUG1105", "UFUG1103", "UFUG1106"]
}
```

The backend converts this into at most one required section, one choice section,
and one elective section. That loses the official grouping semantics. A rule such
as:

```text
(UFUG1102 OR UFUG1105) AND (UFUG1103 OR UFUG1106)
```

is incorrectly rendered and evaluated as one `4 choose 2` pool.

The PDF audit also found requirements that cannot be represented by course-count
arrays alone:

- nested independent choice groups, such as `2 choose 1 + 2 choose 1 + 10 choose 2`;
- credit-based elective requirements;
- combined course-count and credit thresholds;
- source constraints, such as at least four `AMAT` courses;
- courses that appear in both required-choice and elective pools but must not be
  counted twice;
- current progress and projected progress with different status semantics.

## 3. Product Semantics

The Academic Map reports two progress views:

- **Current progress** counts completed and in-progress courses.
- **Projected progress** counts completed, in-progress, and planned courses.

Interested and not-interested courses remain visible where appropriate but never
count toward either progress view.

Within one program, a course is allocated to at most one requirement leaf by
default. A future curriculum may opt into reuse for an explicit node with
`"allow_reuse": true`, but bundled data must not enable reuse unless an official
source explicitly permits it.

The evaluator must find a globally valid allocation. It must not rely on a
first-match greedy pass because overlapping pools and source constraints can make
a locally valid choice produce an invalid overall result.

## 4. Curriculum Data Model

Keep the existing outer `CurriculumRequirementGroup` fields for ordering,
category labels, and compatibility while migrating each group's internal rule to
`rule_tree`.

### 4.1 Node Types

An `all_of` node requires every child node:

```json
{
  "type": "all_of",
  "children": []
}
```

A `required` leaf lists fixed courses:

```json
{
  "type": "required",
  "key": "fixed_courses",
  "label_en": "Required courses",
  "label_zh": "固定必修",
  "courses": ["UFUG2104", "UFUG2601"]
}
```

A `choose` leaf defines a candidate pool and one or both minimum thresholds:

```json
{
  "type": "choose",
  "key": "science_choices",
  "label_en": "Science foundation",
  "label_zh": "理科基础",
  "min_courses": 2,
  "min_credits": 6,
  "courses": ["UFUG1301", "UFUG1302", "UFUG1401"]
}
```

A `choose` leaf may include additional constraints:

```json
{
  "type": "choose",
  "key": "major_electives",
  "label_en": "Major electives",
  "label_zh": "专业选修",
  "min_courses": 5,
  "min_credits": 15,
  "courses": ["AMAT1510", "AMAT2050"],
  "constraints": [
    {
      "type": "course_prefix",
      "value": "AMAT",
      "min_courses": 4
    }
  ]
}
```

Supported first-release constraints are deliberately narrow:

- `course_prefix` with `min_courses`;
- leaf-level `allow_reuse`, defaulting to `false`.

Do not introduce a general-purpose expression language until an official
curriculum source requires it.

### 4.2 DSA 2025 Example

The DSA 2025 fundamental course rule is:

```json
{
  "type": "all_of",
  "children": [
    {
      "type": "choose",
      "key": "calculus_i",
      "min_courses": 1,
      "courses": ["UFUG1102", "UFUG1105"]
    },
    {
      "type": "choose",
      "key": "calculus_ii",
      "min_courses": 1,
      "courses": ["UFUG1103", "UFUG1106"]
    },
    {
      "type": "choose",
      "key": "science_foundation",
      "min_courses": 2,
      "min_credits": 6,
      "courses": [
        "UFUG1301",
        "UFUG1302",
        "UFUG1303",
        "UFUG1401",
        "UFUG1402",
        "UFUG1403",
        "UFUG1501",
        "UFUG1502",
        "UFUG1503",
        "UFUG1504"
      ]
    },
    {
      "type": "choose",
      "key": "intro_cs",
      "min_courses": 1,
      "courses": ["UFUG1601", "UFUG1603"]
    },
    {
      "type": "choose",
      "key": "discrete_math",
      "min_courses": 1,
      "courses": ["UFUG2106", "DSAA2088"]
    },
    {
      "type": "choose",
      "key": "linear_algebra",
      "min_courses": 1,
      "courses": ["UFUG2102", "UFUG2103"]
    },
    {
      "type": "required",
      "key": "fixed_courses",
      "courses": ["UFUG2104", "UFUG2601", "UFUG2602"]
    }
  ]
}
```

## 5. Evaluation

### 5.1 Course Credits

Use the standard catalog credit as the primary value. Use the imported user
record units only as a fallback when the catalog lacks a usable value. Return a
warning and do not claim that a credit-constrained section is satisfied if a
counted candidate still has unknown credits.

The course catalog JSON uses `credit`; the database `Course` model uses
`credits`. The implementation must normalize these existing sources into one
numeric evaluator input.

### 5.2 Allocation

Evaluate each program independently.

For each progress view:

1. Collect eligible user records for that view.
2. Allocate fixed required leaves first.
3. Solve the remaining `choose` leaves as a deterministic global assignment
   problem.
4. Prefer assignments that satisfy the greatest number of required leaves.
5. For equally valid assignments, prefer stricter constrained leaves, then PDF
   order, then normalized course code. This makes output stable across runs.
6. Compute group and section progress from the chosen allocation.

The search space is small for the bundled curricula, so a backtracking solver
with pruning is acceptable and easy to test. Avoid adding an optimization
dependency.

### 5.3 Group Progress

Each evaluated group exposes:

- current and projected satisfaction;
- current and projected counted courses;
- current and projected counted credits;
- section-level progress;
- allocation details for each visible course;
- warnings for incomplete source data.

The outer group's historical `min_courses` and `min_credits` may remain during
migration for compatibility and summary display, but correctness comes from the
evaluated `rule_tree`.

## 6. API Contract

The frontend consumes evaluated sections. It does not parse or evaluate
`rule_tree`.

Example group output:

```json
{
  "key": "fundamental_courses",
  "name_en": "Fundamental Courses",
  "name_zh": "基础课程",
  "category": "fundamental",
  "current": {
    "satisfied": false,
    "counted_courses": 8,
    "required_courses": 10,
    "counted_credits": 25,
    "required_credits": 31
  },
  "projected": {
    "satisfied": true,
    "counted_courses": 10,
    "required_courses": 10,
    "counted_credits": 31,
    "required_credits": 31
  },
  "sections": [
    {
      "key": "calculus_i",
      "kind": "choice",
      "label_en": "Calculus I: choose 1 of 2",
      "label_zh": "微积分 I：2 选 1",
      "current": {
        "satisfied": true,
        "counted_courses": 1,
        "required_courses": 1,
        "counted_credits": 3,
        "required_credits": null
      },
      "projected": {
        "satisfied": true,
        "counted_courses": 1,
        "required_courses": 1,
        "counted_credits": 3,
        "required_credits": null
      },
      "cells": []
    }
  ],
  "warnings": []
}
```

Each cell exposes:

```json
{
  "kind": "course",
  "course_code": "UFUG1105",
  "title": "Honors Calculus I",
  "record_status": "completed",
  "allocation_status": "counted",
  "counted_toward": "calculus_i",
  "credits": 3,
  "credit_source": "catalog",
  "shared_majors": ["AI", "DSA"]
}
```

Supported `allocation_status` values:

- `counted`
- `candidate`
- `planned`
- `excluded_duplicate`
- `missing_credit`

## 7. Frontend Presentation

Keep the current Academic Map layout and blue campus-tool visual language.

### 7.1 Collapsed Group

Do not show a misleading flattened label such as `12 choose 4`.

Show:

- group title;
- current course and credit progress;
- projected course and credit progress when planned courses change the result;
- up to three leaf summary chips and a `+N more` chip.

Example:

```text
基础课程
当前 8 / 10 门 · 25 / 31 学分
计划后 10 / 10 门 · 31 / 31 学分
[微积分 I 1/1] [微积分 II 1/1] [理科基础 1/2] [+4 更多]
```

### 7.2 Expanded Group

Render one card per evaluated leaf. Show the leaf's bilingual label, current
progress, projected progress when different, and its course cards.

Split the existing combined `now` visual status into separate in-progress and
planned statuses. A planned course must not visually imply current completion.

For duplicate candidates, show which section already counted the course. For
credit uncertainty, display a lightweight `credit to confirm` warning.

All new copy belongs in both `front-end/i18n/locales/zh.json` and
`front-end/i18n/locales/en.json`.

## 8. Full PDF Migration Inventory

Rebuild every bundled group from its PDF. The main decompositions are:

| Program and cohort | Required migration details |
| --- | --- |
| AI 2023-24 | Programming `2 choose 1`; two calculus groups; linear algebra `2 choose 1`; science foundation `5 choose 1`; fixed courses |
| AI 2025-26 | Two calculus groups; linear algebra `2 choose 1`; science foundation `6 choose 1`; fixed courses |
| DSA 2023 | Two calculus groups; science foundation `7 choose 2`; interdisciplinary `3 choose 1`; discrete math `2 choose 1`; fixed courses |
| DSA 2024 | Two calculus groups; science foundation `10 choose 2`; programming `2 choose 1`; interdisciplinary `3 choose 1`; discrete math and linear algebra groups; fixed courses |
| DSA 2025-26 | Two calculus groups; science foundation `10 choose 2`; programming, discrete math, and linear algebra groups; fixed courses |
| AMAT 2024-25 | Programming, calculus, physics, and linear algebra groups; fixed courses |
| SMMG 2023-24 | Programming, chemistry, physics, calculus, and linear algebra groups; fixed courses |
| SMMG 2025-26 | Programming-related `4 choose 1 + 3 choose 1`; chemistry, physics, calculus, and linear algebra groups; fixed courses |
| FTEC 2025 | Two calculus groups; programming `2 choose 1`; fixed courses |
| ROAS 2025 | Programming, physics, calculus, and linear algebra groups; fixed courses |
| MICS 2025 | Two calculus groups; mathematics `4 choose 2`; two physics groups; fixed courses |
| SEE 2026 | Programming, physics, and calculus groups; fixed courses; required material course `2 choose 1` |

Major-required groups and major-elective groups must also be rebuilt from the
PDFs, not copied mechanically from the old payload.

## 9. Confirmed Corrections

The migration must include these corrections found during the PDF audit:

- AI 2025: `UFUG2103` belongs to the `UFUG2102 / UFUG2103` linear algebra choice,
  not to fixed required courses.
- AMAT 2024: `UFUG1303` is a fixed course. The chemistry choice is
  `UFUG1301 / UFUG1302`.
- MICS 2025: major electives require `11` courses and `31` credits, not `10`
  courses and `31` credits.
- SEE 2026: major required courses include a separate `SMMG2640 / AMAT3060`
  choice.
- DSA cohorts: each major-required choice pair remains independent. A course
  used as a required course cannot count again toward the elective requirement.
- AMAT 2024: major electives require at least `5` courses and `15` credits,
  including at least `4` `AMAT` courses.

## 10. Compatibility and Rollout

Add `rule_tree` support while retaining a temporary evaluator fallback for the
legacy `required_courses`, `choices`, and `electives` keys. Migrate all bundled
payload entries in the same backend release. The fallback protects older
database rows during deployment and can be removed in a later cleanup after the
startup sync has refreshed production rows.

No database migration is required because `CurriculumRequirementGroup.rule` is
already stored as JSONB.

Deploy backend changes to the backend repository `main` branch after tests pass.
The branch automatically deploys to `https://dev.unikorn.axfff.com`. Verify the
frontend against that test backend from `localhost:3000`, then stop the local
frontend dev server.

## 11. Tests

Backend tests:

- one curriculum payload snapshot assertion per PDF source;
- recursive sync normalization for `rule_tree`;
- current versus projected progress;
- independent choice groups;
- nested `all_of`;
- globally optimal assignment for overlapping pools;
- duplicate exclusion across required and elective leaves;
- credit thresholds;
- catalog-credit priority and imported-unit fallback;
- missing-credit warnings;
- `course_prefix` minimum constraints;
- stable deterministic allocation;
- legacy-rule fallback during rollout.

Frontend checks:

- TypeScript contract update;
- bilingual labels and status copy;
- separate in-progress and planned visuals;
- collapsed leaf summary chips;
- expanded leaf cards;
- duplicate and credit-warning rendering;
- production build;
- local browser verification at `localhost:3000`.

## 12. Non-Goals

- Automatically parsing curriculum PDFs at runtime.
- Changing the prerequisite expression schema.
- Building a general curriculum authoring UI.
- Recommending courses or semesters.
- Switching the frontend away from the configured test backend.
