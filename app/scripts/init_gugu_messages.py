#!/usr/bin/env python3
"""
Initialize Gugu Messages Table
创建咕咕聊天室消息表
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app import create_app
from app.extensions import db
from app.models import GuguMessage

def init_gugu_messages_table():
    """创建咕咕消息表"""
    app = create_app()
    
    with app.app_context():
        try:
            # 创建表
            db.create_all()
            print("✅ 咕咕消息表创建成功")
            
            # 检查表是否存在
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'gugu_messages' in tables:
                print("✅ gugu_messages 表已存在")
                
                # 获取表结构
                columns = inspector.get_columns('gugu_messages')
                print("📋 表结构:")
                for column in columns:
                    print(f"  - {column['name']}: {column['type']}")
            else:
                print("❌ gugu_messages 表创建失败")
                
        except Exception as e:
            print(f"❌ 创建咕咕消息表失败: {e}")
            return False
    
    return True

if __name__ == '__main__':
    print("🚀 开始创建咕咕消息表...")
    success = init_gugu_messages_table()
    
    if success:
        print("✅ 咕咕消息表初始化完成")
    else:
        print("❌ 咕咕消息表初始化失败")
        sys.exit(1)