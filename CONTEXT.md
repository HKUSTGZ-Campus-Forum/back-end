# Course Scheduler Domain Language

- **Course**: The canonical academic identity, such as `UCUG1000`, independent of semester and data source.
- **Offering**: A course made available in one semester.
- **Section**: One selectable class instance within an offering.
- **Meeting**: A weekday/time/location occurrence for a section, bounded by one or more teaching-date ranges when known.
- **Module**: A credit-bearing component inside a modular course, such as `M01`; a student chooses one section for each selected module.
- **Teaching period**: The calendar interval in which a module meets. It may be represented by legacy `layer` values, but it is not a selection group.
- **Selection group**: An academic cardinality rule over modules or sections, such as “M01 exactly once” or “choose exactly two from M02–M07.”
- **Academic credit**: Credit awarded by completing a course.
- **Semester load credit**: The portion of academic credit counted in the scheduler’s semester total. MOES courses currently contribute zero semester load credit.
- **Data source**: Internal provenance for an offering or section. SISN and reviewed KLMS imports remain operationally distinct, while the student-facing scheduler presents both as courses.
