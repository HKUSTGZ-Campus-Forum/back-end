from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.contest import ContestInfo
from app.models.contest_submission import ContestSubmission
from app.models.user import User
from flask_jwt_extended import jwt_required, get_jwt_identity

bp = Blueprint('contest', __name__, url_prefix='/api/contest')


@bp.route('', methods=['GET'])
def get_contest():
    """获取比赛信息（公开接口）"""
    try:
        contest = ContestInfo.query.first()
        if not contest:
            return jsonify({"error": "暂无比赛信息"}), 404
        return jsonify(contest.to_dict()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('', methods=['PUT'])
@jwt_required()
def update_contest():
    """更新比赛信息（管理员接口）"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or not current_user.is_admin():
            return jsonify({"error": "需要管理员权限"}), 403

        data = request.get_json() or {}
        contest = ContestInfo.query.first()
        if not contest:
            contest = ContestInfo()
            db.session.add(contest)

        if 'title' in data:
            contest.title = data['title']
        if 'description' in data:
            contest.description = data['description']
        if 'rules' in data:
            contest.rules = data['rules']
        if 'prizes' in data:
            contest.prizes = data['prizes']
        if 'start_time' in data and data['start_time']:
            from datetime import datetime
            contest.start_time = datetime.fromisoformat(data['start_time'])
        if 'end_time' in data and data['end_time']:
            from datetime import datetime
            contest.end_time = datetime.fromisoformat(data['end_time'])
        if 'is_active' in data:
            contest.is_active = bool(data['is_active'])

        db.session.commit()
        return jsonify(contest.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/submit', methods=['POST'])
@jwt_required()
def submit_project():
    """提交或更新作品"""
    try:
        user_id = get_jwt_identity()
        data = request.get_json() or {}

        contest = ContestInfo.query.first()
        if not contest or not contest.is_active:
            return jsonify({"error": "比赛暂未开放提交"}), 400

        if not data.get('project_name', '').strip():
            return jsonify({"error": "作品名称不能为空"}), 400
        if not data.get('description', '').strip():
            return jsonify({"error": "作品介绍不能为空"}), 400

        existing = ContestSubmission.query.filter_by(user_id=user_id).first()
        if existing:
            existing.project_name = data['project_name'].strip()
            existing.description = data['description'].strip()
            existing.project_url = data.get('project_url', existing.project_url)
            existing.team_members = data.get('team_members', existing.team_members)
            db.session.commit()
            return jsonify({"message": "提交已更新", "submission": existing.to_dict()}), 200

        submission = ContestSubmission(
            user_id=user_id,
            project_name=data['project_name'].strip(),
            description=data['description'].strip(),
            project_url=data.get('project_url'),
            team_members=data.get('team_members'),
        )
        db.session.add(submission)
        db.session.commit()
        return jsonify({"message": "提交成功", "submission": submission.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/my-submission', methods=['GET'])
@jwt_required()
def get_my_submission():
    """获取当前用户的提交"""
    try:
        user_id = get_jwt_identity()
        submission = ContestSubmission.query.filter_by(user_id=user_id).first()
        if not submission:
            return jsonify({"submission": None}), 200
        return jsonify({"submission": submission.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/submissions', methods=['GET'])
@jwt_required()
def get_all_submissions():
    """获取所有提交（管理员接口）"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or not current_user.is_admin():
            return jsonify({"error": "需要管理员权限"}), 403

        submissions = ContestSubmission.query.order_by(
            ContestSubmission.submitted_at.desc()
        ).all()
        return jsonify({
            "submissions": [s.to_dict() for s in submissions],
            "total": len(submissions),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
