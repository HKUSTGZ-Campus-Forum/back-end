from flask import Blueprint, request, jsonify, abort
from app.models.post import Post
from app.models.tag import Tag
from app.models.reaction import Reaction
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc, asc
from datetime import datetime, timedelta
import json

bp = Blueprint('comment', __name__, url_prefix='/comments')