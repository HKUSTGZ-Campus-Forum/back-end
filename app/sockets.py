# app/sockets.py
from flask_socketio import emit, join_room, leave_room
from flask_jwt_extended import decode_token, get_jwt_identity
from flask import request
from .models import User, Post, Comment
from .extensions import db
import json
from datetime import datetime


def register_socket_events(socketio):
    """Register all WebSocket event handlers"""
    
    @socketio.on('connect')
    def handle_connect(auth):
        """Handle client connection"""
        print(f"Client connected: {request.sid}")
        
        # Optional: Authenticate user on connect
        token = None
        if auth and isinstance(auth, dict):
            token = auth.get('token')
        
        if token:
            try:
                decoded_token = decode_token(token)
                user_id = decoded_token['sub']
                user = User.query.get(user_id)
                if user:
                    # Join user to instant discussion room
                    join_room('instant_discussion')
                    emit('connection_status', {
                        'status': 'authenticated',
                        'user_id': user_id,
                        'username': user.username
                    })
                    print(f"User {user.username} joined instant discussion")
                else:
                    emit('connection_status', {'status': 'invalid_user'})
            except Exception as e:
                print(f"Auth error: {e}")
                emit('connection_status', {'status': 'auth_error'})
        else:
            # Allow anonymous users to listen
            join_room('instant_discussion')
            emit('connection_status', {'status': 'anonymous'})
    
    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle client disconnection"""
        print(f"Client disconnected: {request.sid}")
        leave_room('instant_discussion')
    
    @socketio.on('join_instant_discussion')
    def handle_join_discussion():
        """Join the instant discussion room"""
        join_room('instant_discussion')
        emit('joined_discussion', {'status': 'success'})
    
    @socketio.on('send_message')
    def handle_send_message(data):
        """Handle new instant discussion message"""
        try:
            # Authenticate user
            token = data.get('token')
            if not token:
                emit('message_error', {'error': 'Authentication required'})
                return
            
            decoded_token = decode_token(token)
            user_id = decoded_token['sub']
            user = User.query.get(user_id)
            
            if not user:
                emit('message_error', {'error': 'Invalid user'})
                return
            
            content = data.get('content', '').strip()
            if not content:
                emit('message_error', {'error': 'Message content required'})
                return
            
            if len(content) > 500:
                emit('message_error', {'error': 'Message too long (max 500 characters)'})
                return
            
            # Get or create instant discussion post
            instant_post = Post.query.join(Post.tags).filter_by(name='instant-discussion').first()
            if not instant_post:
                emit('message_error', {'error': 'Instant discussion not initialized'})
                return
            
            # Create new comment (message)
            new_comment = Comment(
                content=content,
                user_id=user_id,
                post_id=instant_post.id
            )
            
            db.session.add(new_comment)
            db.session.commit()
            
            # Prepare message data for broadcasting
            message_data = {
                'id': new_comment.id,
                'content': new_comment.content,
                'created_at': new_comment.created_at.isoformat(),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'avatar_url': user.avatar_url if hasattr(user, 'avatar_url') else None
                }
            }
            
            # Broadcast to all users in instant discussion room
            socketio.emit('new_message', message_data, room='instant_discussion')
            
            print(f"Message sent by {user.username}: {content[:50]}...")
            
        except Exception as e:
            print(f"Error handling message: {e}")
            emit('message_error', {'error': 'Failed to send message'})
    
    @socketio.on('typing_start')
    def handle_typing_start(data):
        """Handle user starting to type"""
        try:
            token = data.get('token')
            if not token:
                return
            
            decoded_token = decode_token(token)
            user_id = decoded_token['sub']
            user = User.query.get(user_id)
            
            if user:
                # Broadcast typing indicator to others in room (exclude sender)
                socketio.emit('user_typing', {
                    'user_id': user_id,
                    'username': user.username,
                    'typing': True
                }, room='instant_discussion', include_self=False)
                
        except Exception as e:
            print(f"Error handling typing start: {e}")
    
    @socketio.on('typing_stop')
    def handle_typing_stop(data):
        """Handle user stopping typing"""
        try:
            token = data.get('token')
            if not token:
                return
            
            decoded_token = decode_token(token)
            user_id = decoded_token['sub']
            user = User.query.get(user_id)
            
            if user:
                # Broadcast stop typing to others in room (exclude sender)
                socketio.emit('user_typing', {
                    'user_id': user_id,
                    'username': user.username,
                    'typing': False
                }, room='instant_discussion', include_self=False)
                
        except Exception as e:
            print(f"Error handling typing stop: {e}")
    
    return socketio