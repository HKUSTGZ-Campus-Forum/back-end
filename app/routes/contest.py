from typing import Optional
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify, Response
from app.extensions import db
from app.models.contest import ContestInfo
from app.models.contest_submission import ContestSubmission, TRACK_FUN, TRACK_TECH
from app.models.contest_organizer import ContestOrganizer
from app.models.user import User
from flask_jwt_extended import jwt_required, get_jwt_identity

bp = Blueprint('contest', __name__, url_prefix='/contest')

# 报名占位记录专用队名（与「正式队名」区分）
_PLACEHOLDER_TEAM_NAME = '待提交'
# 正式提交不再收集作品介绍，数据库列保留，写入占位符
_SUBMISSION_DESC_PLACEHOLDER = '-'
ALLOWED_TRACKS = (TRACK_TECH, TRACK_FUN)


def _submissions_dict_for_user(user_id) -> dict:
    out = {TRACK_TECH: None, TRACK_FUN: None}
    for r in ContestSubmission.query.filter_by(user_id=user_id).all():
        out[r.track] = r.to_dict()
    return out


def _parse_track(data: dict) -> Optional[str]:
    t = (data.get('track') or '').strip()
    return t if t in ALLOWED_TRACKS else None


def _is_placeholder_registration(data: dict) -> bool:
    return (data.get('project_name') or '').strip() == _PLACEHOLDER_TEAM_NAME


def _validate_placeholder_registration(data: dict) -> Optional[str]:
    if not (data.get('project_name') or '').strip():
        return '参数无效'
    if not (data.get('description') or '').strip():
        return '报名说明不能为空'
    return None


def _looks_like_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except Exception:
        return False


def _validate_real_submission(data: dict) -> Optional[str]:
    name = (data.get('project_name') or '').strip()
    if not name:
        return '队名不能为空'
    if name == _PLACEHOLDER_TEAM_NAME:
        return '队名不能使用保留词「待提交」'
    url = (data.get('project_url') or '').strip()
    if not url:
        return '项目链接不能为空'
    if not _looks_like_http_url(url):
        return '项目链接需为有效的 http(s) 地址'
    members = (data.get('team_members') or '').strip()
    if not members:
        return '团队成员不能为空'
    return None


# ── 权限辅助 ──────────────────────────────────────────────────

def _get_contest():
    return ContestInfo.query.first()

def _is_manager(user: User) -> bool:
    """admin 或当前比赛的 organizer 均视为管理者"""
    if user.is_admin():
        return True
    contest = _get_contest()
    if contest and contest.is_organizer(user.id):
        return True
    return False


# ── 公开接口 ──────────────────────────────────────────────────

@bp.route('', methods=['GET'])
def get_contest():
    """获取比赛信息（公开）"""
    try:
        contest = _get_contest()
        if not contest:
            return jsonify({"error": "暂无比赛信息"}), 404
        return jsonify(contest.to_dict()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 登录用户接口 ───────────────────────────────────────────────

@bp.route('/my-role', methods=['GET'])
@jwt_required()
def get_my_role():
    """返回当前用户在本次比赛中的角色信息"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user:
            return jsonify({"error": "用户不存在"}), 404
        contest = _get_contest()
        is_organizer = bool(contest and contest.is_organizer(user.id))
        return jsonify({
            "is_admin": user.is_admin(),
            "is_organizer": is_organizer,
            "is_manager": user.is_admin() or is_organizer,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/my-submission', methods=['GET'])
@jwt_required()
def get_my_submission():
    """获取当前用户两条赛道的提交（tech / fun）。"""
    try:
        user_id = get_jwt_identity()
        if not ContestSubmission.query.filter_by(user_id=user_id).first():
            return jsonify({"submissions": {TRACK_TECH: None, TRACK_FUN: None}}), 200
        return jsonify({"submissions": _submissions_dict_for_user(user_id)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _upsert_placeholder_track(user_id, track: str, description_text: str) -> ContestSubmission:
    row = ContestSubmission.query.filter_by(user_id=user_id, track=track).first()
    if row:
        row.project_name = _PLACEHOLDER_TEAM_NAME
        row.description = description_text
        row.project_url = None
        row.team_members = None
        return row
    row = ContestSubmission(
        user_id=user_id,
        track=track,
        project_name=_PLACEHOLDER_TEAM_NAME,
        description=description_text,
        project_url=None,
        team_members=None,
    )
    db.session.add(row)
    return row


@bp.route('/submit', methods=['POST'])
@jwt_required()
def submit_project():
    """双赛道：报名一次写两条占位；正式提交须带 track（tech 或 fun）。"""
    try:
        user_id = get_jwt_identity()
        data = request.get_json() or {}

        contest = _get_contest()
        if not contest or not contest.is_active:
            return jsonify({"error": "比赛暂未开放提交"}), 400

        # ── 报名：双赛道占位（请求体不含 track 或 track 非法且为占位队名）──
        if _is_placeholder_registration(data) and _parse_track(data) is None:
            err = _validate_placeholder_registration(data)
            if err:
                return jsonify({"error": err}), 400
            desc = data['description'].strip()
            for tr in ALLOWED_TRACKS:
                _upsert_placeholder_track(user_id, tr, desc)
            db.session.commit()
            return jsonify({
                "message": "报名成功",
                "submissions": _submissions_dict_for_user(user_id),
            }), 200

        # ── 单赛道占位更新（兼容带 track 的占位请求）──
        if _is_placeholder_registration(data):
            tr = _parse_track(data)
            if tr is None:
                return jsonify({"error": "无效请求"}), 400
            err = _validate_placeholder_registration(data)
            if err:
                return jsonify({"error": err}), 400
            _upsert_placeholder_track(user_id, tr, data['description'].strip())
            db.session.commit()
            return jsonify({
                "message": "提交已更新",
                "submissions": _submissions_dict_for_user(user_id),
            }), 200

        # ── 正式提交 ──
        tr = _parse_track(data)
        if tr is None:
            return jsonify({"error": "请选择赛道（tech 或 fun）"}), 400
        err = _validate_real_submission(data)
        if err:
            return jsonify({"error": err}), 400

        existing_one = ContestSubmission.query.filter_by(user_id=user_id, track=tr).first()
        if existing_one:
            existing_one.project_name = data['project_name'].strip()
            existing_one.description = (data.get('description') or '').strip() or _SUBMISSION_DESC_PLACEHOLDER
            existing_one.project_url = data['project_url'].strip()
            existing_one.team_members = data['team_members'].strip()
            db.session.commit()
            return jsonify({
                "message": "提交已更新",
                "submission": existing_one.to_dict(),
                "submissions": _submissions_dict_for_user(user_id),
            }), 200

        submission = ContestSubmission(
            user_id=user_id,
            track=tr,
            project_name=data['project_name'].strip(),
            description=(data.get('description') or '').strip() or _SUBMISSION_DESC_PLACEHOLDER,
            project_url=data['project_url'].strip(),
            team_members=data['team_members'].strip(),
        )
        db.session.add(submission)
        db.session.commit()
        return jsonify({
            "message": "提交成功",
            "submission": submission.to_dict(),
            "submissions": _submissions_dict_for_user(user_id),
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ── 管理者接口（admin 或 organizer） ──────────────────────────

@bp.route('', methods=['PUT'])
@jwt_required()
def update_contest():
    """更新比赛信息（admin 或 organizer）"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        data = request.get_json() or {}
        contest = _get_contest()
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
        if 'announcements' in data:
            contest.announcements = data['announcements']
        if 'is_active' in data:
            contest.is_active = bool(data['is_active'])

        db.session.commit()
        return jsonify(contest.to_dict(include_organizers=True)), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/submissions', methods=['GET'])
@jwt_required()
def get_all_submissions():
    """获取所有提交（admin 或 organizer）"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        submissions = ContestSubmission.query.order_by(
            ContestSubmission.user_id,
            ContestSubmission.track,
            ContestSubmission.submitted_at.desc(),
        ).all()
        return jsonify({
            "submissions": [s.to_dict() for s in submissions],
            "total": len(submissions),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/organizers', methods=['GET'])
@jwt_required()
def get_organizers():
    """获取 organizer 列表（admin 或 organizer）"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        contest = _get_contest()
        if not contest:
            return jsonify({"organizers": []}), 200

        organizers = ContestOrganizer.query.filter_by(contest_id=contest.id).all()
        return jsonify({
            "organizers": [o.to_dict() for o in organizers]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/organizers', methods=['POST'])
@jwt_required()
def add_organizer():
    """添加 organizer（admin 或 organizer）"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        data = request.get_json() or {}
        target_user_id = data.get('user_id')
        if not target_user_id:
            return jsonify({"error": "user_id 不能为空"}), 400

        target_user = User.query.get(target_user_id)
        if not target_user or target_user.is_deleted:
            return jsonify({"error": "用户不存在"}), 404

        contest = _get_contest()
        if not contest:
            return jsonify({"error": "比赛不存在，请先创建比赛信息"}), 404

        existing = ContestOrganizer.query.filter_by(
            contest_id=contest.id, user_id=target_user_id
        ).first()
        if existing:
            return jsonify({"error": "该用户已经是 organizer"}), 409

        organizer = ContestOrganizer(contest_id=contest.id, user_id=target_user_id)
        db.session.add(organizer)
        db.session.commit()
        return jsonify({"message": "已添加为 organizer", "organizer": organizer.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/organizers/<int:target_user_id>', methods=['DELETE'])
@jwt_required()
def remove_organizer(target_user_id: int):
    """移除 organizer（admin 或 organizer）"""
    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        contest = _get_contest()
        if not contest:
            return jsonify({"error": "比赛不存在"}), 404

        organizer = ContestOrganizer.query.filter_by(
            contest_id=contest.id, user_id=target_user_id
        ).first()
        if not organizer:
            return jsonify({"error": "该用户不是 organizer"}), 404

        db.session.delete(organizer)
        db.session.commit()
        return jsonify({"message": "已移除 organizer"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/submissions/export', methods=['GET'])
@jwt_required()
def export_submissions_csv():
    """导出提交列表为 CSV（admin 或 organizer）"""
    import csv
    import io

    try:
        user = User.query.get(get_jwt_identity())
        if not user or not _is_manager(user):
            return jsonify({"error": "需要管理者权限"}), 403

        submissions = ContestSubmission.query.order_by(
            ContestSubmission.user_id,
            ContestSubmission.track,
            ContestSubmission.submitted_at.desc(),
        ).all()

        output = io.StringIO()
        output.write('\ufeff')  # BOM for Excel
        writer = csv.writer(output)
        writer.writerow(['#', '用户名', 'UID', '赛道', '队名', '项目链接', '团队成员', '提交时间', '最后更新'])

        for idx, sub in enumerate(submissions, 1):
            d = sub.to_dict()
            writer.writerow([
                idx,
                sub.user.username if sub.user else '',
                sub.user_id,
                d.get('track_label') or sub.track,
                sub.project_name,
                sub.project_url or '',
                sub.team_members or '',
                sub.submitted_at.isoformat() if sub.submitted_at else '',
                sub.updated_at.isoformat() if sub.updated_at else '',
            ])

        csv_data = output.getvalue()
        output.close()

        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=contest_submissions.csv'},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
