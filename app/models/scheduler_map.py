from app.extensions import db


class SchedulerMapComponent(db.Model):
    __tablename__ = 'scheduler_map_components'

    id = db.Column(db.String(255), primary_key=True)
    node_type = db.Column(db.Boolean, nullable=True)
    x_coordinate = db.Column(db.Integer, nullable=False)
    y_coordinate = db.Column(db.Integer, nullable=False)
    category = db.Column(db.Integer, nullable=False)


class SchedulerMapLine(db.Model):
    __tablename__ = 'scheduler_map_lines'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    start_id = db.Column(db.String(255), db.ForeignKey('scheduler_map_components.id', ondelete='CASCADE'), nullable=False)
    end_id = db.Column(db.String(255), db.ForeignKey('scheduler_map_components.id', ondelete='CASCADE'), nullable=False)
    line_type = db.Column(db.Boolean, nullable=True)
    x_coordinate = db.Column(db.Integer, nullable=False)
    category = db.Column(db.Integer, nullable=False)

    start_component = db.relationship('SchedulerMapComponent', foreign_keys=[start_id],
                                       backref=db.backref('start_lines', lazy='dynamic'))
    end_component = db.relationship('SchedulerMapComponent', foreign_keys=[end_id],
                                     backref=db.backref('end_lines', lazy='dynamic'))
