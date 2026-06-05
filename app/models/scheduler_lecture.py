from app.extensions import db


class SchedulerLecture(db.Model):
    __tablename__ = 'scheduler_lectures'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    semester_id = db.Column(db.String(32), nullable=False)
    section_id = db.Column(db.String(16), nullable=False)
    day = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Integer, nullable=False)
    end_time = db.Column(db.Integer, nullable=False)
    room = db.Column(db.String(255), nullable=False)
    instructor = db.Column(db.String(255), nullable=False)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['semester_id', 'section_id'],
            ['scheduler_sections.semester_id', 'scheduler_sections.section_id'],
            ondelete='CASCADE'
        ),
        db.Index('idx_scheduler_lectures_section', 'semester_id', 'section_id'),
    )
