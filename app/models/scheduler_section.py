from app.extensions import db


class SchedulerSection(db.Model):
    __tablename__ = 'scheduler_sections'

    semester_id = db.Column(db.String(16), primary_key=True)
    section_id = db.Column(db.String(16), primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    bundle = db.Column(db.Integer, nullable=False)
    layer = db.Column(db.Integer, default=0, nullable=False)
    quota = db.Column(db.Integer, nullable=False)
    section_type = db.Column(db.String(16), nullable=False)
    is_main = db.Column(db.Boolean, default=False, nullable=False)

    course = db.relationship('Course', backref=db.backref('scheduler_sections', lazy='dynamic'))
    lectures = db.relationship('SchedulerLecture', backref='section', lazy='dynamic',
                               foreign_keys='SchedulerLecture.semester_id, SchedulerLecture.section_id',
                               primaryjoin='and_(SchedulerSection.semester_id==SchedulerLecture.semester_id, '
                                           'SchedulerSection.section_id==SchedulerLecture.section_id)')

    __table_args__ = (
        db.Index('idx_scheduler_sections_course', 'course_id', 'semester_id'),
    )
