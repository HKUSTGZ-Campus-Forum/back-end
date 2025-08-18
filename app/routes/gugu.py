from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from app.models import GuguMessage, User
from app.extensions import db
from datetime import datetime

gugu_bp = Blueprint('gugu', __name__, url_prefix='/gugu')

@gugu_bp.route('/messages', methods=['GET'])
def get_messages():
    """获取咕咕聊天消息"""
    try:
        # 获取查询参数
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 100)  # 限制最大获取数量
        
        # 获取消息
        messages = GuguMessage.get_recent_messages(limit=limit)
        
        # 反转列表以获得正确的时间顺序（旧消息在前）
        messages = list(reversed(messages))
        
        # 转换为字典格式
        messages_data = [message.to_dict() for message in messages]
        
        return jsonify({
            'success': True,
            'messages': messages_data,
            'count': len(messages_data)
        }), 200
        
    except Exception as e:
        print(f"❌ 获取咕咕消息失败: {e}")
        return jsonify({
            'success': False,
            'message': '获取消息失败',
            'error': str(e)
        }), 500

@gugu_bp.route('/recent', methods=['GET'])
def get_recent_messages():
    """获取最近的咕咕消息（用于主页预览）"""
    try:
        # 获取查询参数
        limit = request.args.get('limit', 3, type=int)
        limit = min(limit, 10)  # 限制最大获取数量
        
        # 获取最近的消息
        messages = GuguMessage.get_recent_messages(limit=limit)
        
        # 转换为字典格式
        messages_data = [message.to_dict() for message in messages]
        
        return jsonify({
            'success': True,
            'messages': messages_data,
            'count': len(messages_data)
        }), 200
        
    except Exception as e:
        print(f"❌ 获取最近咕咕消息失败: {e}")
        return jsonify({
            'success': False,
            'message': '获取最近消息失败',
            'error': str(e)
        }), 500

@gugu_bp.route('/messages', methods=['POST'])
@jwt_required()
def send_message():
    """发送咕咕聊天消息"""
    try:
        # 获取当前用户
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({
                'success': False,
                'message': '用户不存在'
            }), 404
        
        # 获取请求数据
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': '请求数据格式错误'
            }), 400
        
        content = data.get('content', '').strip()
        if not content:
            return jsonify({
                'success': False,
                'message': '消息内容不能为空'
            }), 400
        
        # 检查消息长度
        if len(content) > 1000:
            return jsonify({
                'success': False,
                'message': '消息内容不能超过1000个字符'
            }), 400
        
        # 创建新消息
        message = GuguMessage.create_message(
            content=content,
            author_id=current_user_id
        )
        
        return jsonify({
            'success': True,
            'message': '消息发送成功',
            'data': message.to_dict()
        }), 201
        
    except Exception as e:
        print(f"❌ 发送咕咕消息失败: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': '发送消息失败',
            'error': str(e)
        }), 500

@gugu_bp.route('/messages/<int:message_id>', methods=['DELETE'])
@jwt_required()
def delete_message(message_id):
    """删除咕咕聊天消息（仅消息作者可删除）"""
    try:
        # 获取当前用户
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({
                'success': False,
                'message': '用户不存在'
            }), 404
        
        # 查找消息
        message = GuguMessage.query.get(message_id)
        if not message:
            return jsonify({
                'success': False,
                'message': '消息不存在'
            }), 404
        
        # 检查权限（只有消息作者可以删除）
        if message.author_id != current_user_id:
            return jsonify({
                'success': False,
                'message': '没有权限删除此消息'
            }), 403
        
        # 删除消息
        db.session.delete(message)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '消息删除成功'
        }), 200
        
    except Exception as e:
        print(f"❌ 删除咕咕消息失败: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': '删除消息失败',
            'error': str(e)
        }), 500

@gugu_bp.route('/stats', methods=['GET'])
def get_chat_stats():
    """获取咕咕聊天室统计信息"""
    try:
        # 获取总消息数
        total_messages = GuguMessage.query.count()
        
        # 获取今日消息数
        today = datetime.now().date()
        today_messages = GuguMessage.query.filter(
            db.func.date(GuguMessage.created_at) == today
        ).count()
        
        # 获取活跃用户数（最近发过消息的用户）
        active_users = db.session.query(GuguMessage.author_id).distinct().count()
        
        return jsonify({
            'success': True,
            'stats': {
                'total_messages': total_messages,
                'today_messages': today_messages,
                'active_users': active_users
            }
        }), 200
        
    except Exception as e:
        print(f"❌ 获取咕咕统计信息失败: {e}")
        return jsonify({
            'success': False,
            'message': '获取统计信息失败',
            'error': str(e)
        }), 500