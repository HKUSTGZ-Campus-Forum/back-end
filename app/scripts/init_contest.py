"""
初始化比赛数据脚本
- 若 contest_info 表中无记录，则创建默认比赛记录
- 将 UID=6（Mount）设为本次比赛的 organizer

使用方式（在 back-end 目录下，激活虚拟环境后执行）：
    python -m app.scripts.init_contest
"""

from app import create_app
from app.extensions import db
from app.models.contest import ContestInfo
from app.models.contest_organizer import ContestOrganizer
from app.models.user import User
from datetime import datetime, timezone

ORGANIZER_UID = 6  # Mount


def init_contest():
    app = create_app()
    with app.app_context():

        # ── 1. 创建比赛记录（如不存在） ──────────────────────────
        contest = ContestInfo.query.first()
        if contest:
            print(f"比赛记录已存在：id={contest.id}，标题="{contest.title}"")
        else:
            # 开始：2026-04-14 10:00 CST = UTC+8，即 UTC 02:00
            # 结束：2026-04-21 00:00 CST = UTC 2026-04-20 16:00
            contest = ContestInfo(
                title='百块奖金Web大赛',
                description='',
                rules='',
                prizes='',
                start_time=datetime(2026, 4, 14, 2, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc),
                is_active=False,  # 默认关闭，管理员确认后在后台开启
            )
            db.session.add(contest)
            db.session.flush()  # 获取 contest.id
            print(f"已创建比赛记录：id={contest.id}")

        # ── 2. 设置 organizer ────────────────────────────────────
        user = User.query.get(ORGANIZER_UID)
        if not user:
            print(f"⚠️  UID={ORGANIZER_UID} 的用户不存在，请确认用户 ID 正确")
        else:
            existing = ContestOrganizer.query.filter_by(
                contest_id=contest.id, user_id=ORGANIZER_UID
            ).first()
            if existing:
                print(f"用户 {user.username}（UID={ORGANIZER_UID}）已经是 organizer，无需重复添加")
            else:
                organizer = ContestOrganizer(contest_id=contest.id, user_id=ORGANIZER_UID)
                db.session.add(organizer)
                print(f"已将用户 {user.username}（UID={ORGANIZER_UID}）设为 organizer")

        db.session.commit()
        print("初始化完成 ✅")


if __name__ == '__main__':
    init_contest()
