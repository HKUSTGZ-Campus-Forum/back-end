from app.extensions import db


class SchedulerUserCourseCart(db.Model):
    __tablename__ = 'scheduler_user_course_carts'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    semester_id = db.Column(db.String(32), primary_key=True)
    course_code = db.Column(db.String(16), primary_key=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship('User', backref=db.backref('scheduler_carts', lazy='dynamic'))
    bundles = db.relationship('SchedulerUserBundleCart', backref='course_cart', lazy='dynamic',
                              cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('idx_scheduler_cart_user_semester', 'user_id', 'semester_id'),
    )


class SchedulerUserBundleCart(db.Model):
    __tablename__ = 'scheduler_user_bundle_carts'

    user_id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.String(32), primary_key=True)
    course_code = db.Column(db.String(16), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    layer = db.Column(db.Integer, default=0, primary_key=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['user_id', 'semester_id', 'course_code'],
            ['scheduler_user_course_carts.user_id', 'scheduler_user_course_carts.semester_id',
             'scheduler_user_course_carts.course_code'],
            ondelete='CASCADE'
        ),
    )
