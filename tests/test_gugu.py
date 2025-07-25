#!/usr/bin/env python3
"""
Test Gugu Chat API
测试咕咕聊天API
"""

import unittest
import json
from app import create_app
from app.extensions import db
from app.models import User, GuguMessage
from flask_jwt_extended import create_access_token

class TestGuguAPI(unittest.TestCase):
    
    def setUp(self):
        """测试前准备"""
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()
        
        with self.app.app_context():
            db.create_all()
            
            # 创建测试用户
            self.test_user = User(
                username='testuser',
                email='test@example.com',
                password_hash='hashed_password'
            )
            db.session.add(self.test_user)
            db.session.commit()
            
            # 生成访问令牌
            self.access_token = create_access_token(identity=self.test_user.id)
    
    def tearDown(self):
        """测试后清理"""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
    
    def test_get_messages_empty(self):
        """测试获取空消息列表"""
        with self.app.app_context():
            response = self.client.get('/api/gugu/messages')
            self.assertEqual(response.status_code, 200)
            
            data = json.loads(response.data)
            self.assertTrue(data['success'])
            self.assertEqual(len(data['messages']), 0)
    
    def test_send_message_without_auth(self):
        """测试未认证用户发送消息"""
        response = self.client.post('/api/gugu/messages', 
                                   json={'content': 'Hello World'})
        self.assertEqual(response.status_code, 401)
    
    def test_send_message_with_auth(self):
        """测试认证用户发送消息"""
        with self.app.app_context():
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = self.client.post('/api/gugu/messages',
                                       json={'content': 'Hello World'},
                                       headers=headers)
            self.assertEqual(response.status_code, 201)
            
            data = json.loads(response.data)
            self.assertTrue(data['success'])
            self.assertEqual(data['data']['content'], 'Hello World')
    
    def test_send_empty_message(self):
        """测试发送空消息"""
        with self.app.app_context():
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = self.client.post('/api/gugu/messages',
                                       json={'content': ''},
                                       headers=headers)
            self.assertEqual(response.status_code, 400)
    
    def test_send_long_message(self):
        """测试发送过长消息"""
        with self.app.app_context():
            headers = {'Authorization': f'Bearer {self.access_token}'}
            long_content = 'x' * 1001  # 超过1000字符限制
            response = self.client.post('/api/gugu/messages',
                                       json={'content': long_content},
                                       headers=headers)
            self.assertEqual(response.status_code, 400)
    
    def test_get_recent_messages(self):
        """测试获取最近消息"""
        with self.app.app_context():
            # 创建测试消息
            message = GuguMessage.create_message(
                content='Test message',
                author_id=self.test_user.id
            )
            
            response = self.client.get('/api/gugu/recent')
            self.assertEqual(response.status_code, 200)
            
            data = json.loads(response.data)
            self.assertTrue(data['success'])
            self.assertEqual(len(data['messages']), 1)
            self.assertEqual(data['messages'][0]['content'], 'Test message')
    
    def test_get_chat_stats(self):
        """测试获取聊天统计"""
        with self.app.app_context():
            response = self.client.get('/api/gugu/stats')
            self.assertEqual(response.status_code, 200)
            
            data = json.loads(response.data)
            self.assertTrue(data['success'])
            self.assertIn('total_messages', data['stats'])
            self.assertIn('today_messages', data['stats'])
            self.assertIn('active_users', data['stats'])

if __name__ == '__main__':
    unittest.main()