# app/routes/auth.py
from flask import Blueprint, request, jsonify
from app.models.user import User
from app.extensions import db
from flask_jwt_extended import create_access_token

bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    if not data.get('username') or not data.get('password'):
        return jsonify({"msg": "Username and password required"}), 400

    user = User(username=data['username'], email=data.get('email', ''))
    user.set_password(data['password'])
    db.session.add(user)
    db.session.commit()

    return jsonify({"msg": "User registered successfully"}), 201

@bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    user = User.query.filter_by(username=data.get('username')).first()
    if user is None or not user.check_password(data.get('password')):
        return jsonify({"msg": "Invalid username or password"}), 401

    access_token = create_access_token(identity=user.id)
    return jsonify({"access_token": access_token}), 200
