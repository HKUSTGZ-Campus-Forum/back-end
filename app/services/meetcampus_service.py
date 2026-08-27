"""Private-beta access rules and guided sandbox content for MeetCampus."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from flask import current_app

from app.models.user import User


def normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def configured_beta_emails() -> frozenset[str]:
    configured = current_app.config.get("MEETCAMPUS_BETA_EMAILS", ())
    if isinstance(configured, str):
        values: Iterable[str] = configured.split(",")
    else:
        values = configured or ()
    return frozenset(normalize_email(value) for value in values if normalize_email(value))


def can_access_meetcampus(user: User | None) -> bool:
    if user is None or user.is_deleted or not user.email_verified:
        return False
    email = normalize_email(user.email)
    return bool(email and email in configured_beta_emails())


MEETCAMPUS_BOOTSTRAP: dict[str, Any] = {
    "feature": {
        "id": "meetcampus",
        "stage": "private_beta",
        "mode": "guided_sandbox",
        "sessionStorage": "browser_local",
        "liveAgents": False,
        "realPeople": False,
        "autonomousAgentDecisions": False,
    },
    "locations": [
        {
            "id": "library",
            "name": {"zh": "图书馆", "en": "Library"},
            "kind": "study",
            "x": 25,
            "y": 19,
        },
        {
            "id": "student-center",
            "name": {"zh": "学生活动中心", "en": "Student Center"},
            "kind": "activity",
            "x": 75,
            "y": 33,
        },
        {
            "id": "teaching-building",
            "name": {"zh": "第一教学楼", "en": "Teaching Building 1"},
            "kind": "study",
            "x": 21,
            "y": 57,
        },
        {
            "id": "ginkgo-canteen",
            "name": {"zh": "银杏食堂", "en": "Ginkgo Canteen"},
            "kind": "dining",
            "x": 50,
            "y": 59,
        },
        {
            "id": "innovation-lab",
            "name": {"zh": "创新实验室", "en": "Innovation Lab"},
            "kind": "study",
            "x": 75,
            "y": 69,
        },
    ],
    "scenarios": [
        {
            "id": "study",
            "label": {"zh": "找自习搭子", "en": "Find a study partner"},
            "summary": {"zh": "低压力地一起专注一会儿", "en": "Share a low-pressure focus session"},
            "icon": "book-open",
            "locationId": "library",
            "candidate": {
                "displayName": {"zh": "周予安", "en": "Zhou Yu'an"},
                "agentName": {"zh": "蓝点", "en": "Blue Dot"},
                "headline": {"zh": "大二 · 数据科学", "en": "Year 2 · Data Science"},
                "bio": {
                    "zh": "喜欢安静的并肩学习，也愿意在卡住时一起拆题。",
                    "en": "Enjoys quiet co-working and talking through a problem when someone gets stuck.",
                },
            },
            "matchReasons": [
                {"zh": "都偏好低压力的一对一相处", "en": "Both prefer low-pressure one-to-one time"},
                {"zh": "晚饭前都有一段空闲时间", "en": "Both have a free window before dinner"},
                {"zh": "专注节奏相近，偶尔交流即可", "en": "Similar focus rhythm with occasional conversation"},
            ],
            "event": {
                "title": {"zh": "最后两张空桌", "en": "The last two open tables"},
                "description": {
                    "zh": "两位沙盒 Agent 同时到达图书馆，只剩窗边安静位和白板讨论桌。",
                    "en": "Both sandbox Agents reach the library as only a quiet window seat and a whiteboard table remain.",
                },
            },
            "choices": [
                {
                    "id": "window",
                    "label": {"zh": "选择窗边安静位", "en": "Choose the quiet window seats"},
                    "description": {"zh": "先用陪伴建立舒适感", "en": "Start with comfortable, quiet company"},
                },
                {
                    "id": "whiteboard",
                    "label": {"zh": "选择白板讨论桌", "en": "Choose the whiteboard table"},
                    "description": {"zh": "给自然交流留一个入口", "en": "Leave room for an easy conversation"},
                },
            ],
            "stories": {
                "window": {
                    "title": {"zh": "窗边的二十五分钟", "en": "Twenty-five minutes by the window"},
                    "summary": {"zh": "两个 Agent 安静完成各自的任务，只在休息时交换了一份复习清单。", "en": "The two Agents quietly finished their tasks and exchanged a revision checklist during the break."},
                    "myAgent": {"zh": "把专注计时器调成双方都舒服的节奏。", "en": "Set a focus timer that suited both people."},
                    "otherAgent": {"zh": "分享了一个把难题拆成小步骤的方法。", "en": "Shared a method for breaking hard problems into small steps."},
                    "commonGround": {"zh": "都不需要用持续聊天来证明友好。", "en": "Neither needs constant conversation to feel welcome."},
                    "difference": {"zh": "你喜欢先列清单，对方习惯先动手再整理。", "en": "You like making a list first; they start and organize later."},
                    "icebreaker": {"zh": "你最近最想解决的一件小事是什么？", "en": "What is one small thing you most want to solve this week?"},
                },
                "whiteboard": {
                    "title": {"zh": "白板上的一道难题", "en": "One problem on the whiteboard"},
                    "summary": {"zh": "一次短暂的卡顿变成了共同拆题，两位 Agent 留下了不同但互补的思路。", "en": "A brief sticking point became a shared problem-solving session with two complementary approaches."},
                    "myAgent": {"zh": "把问题画成了三个清晰的步骤。", "en": "Turned the problem into three clear steps."},
                    "otherAgent": {"zh": "补上了一个更直观的例子。", "en": "Added a more intuitive example."},
                    "commonGround": {"zh": "都愿意承认暂时不会，再一起找办法。", "en": "Both are comfortable admitting uncertainty and looking for a way forward."},
                    "difference": {"zh": "你更结构化，对方更依赖直觉。", "en": "You are more structured; they lean more on intuition."},
                    "icebreaker": {"zh": "你更喜欢先看例子，还是先看原理？", "en": "Do you prefer seeing an example first or the principle first?"},
                },
            },
            "times": [
                {"id": "today-1730", "label": {"zh": "今天 17:30", "en": "Today, 17:30"}},
                {"id": "tomorrow-1030", "label": {"zh": "明天 10:30", "en": "Tomorrow, 10:30"}},
                {"id": "friday-1530", "label": {"zh": "周五 15:30", "en": "Friday, 15:30"}},
            ],
            "durationMinutes": 30,
            "offlineTask": {"zh": "各自带一道最近卡住的问题，交换一次解题思路。", "en": "Each bring one recent sticking point and exchange one way to approach it."},
        },
        {
            "id": "dining",
            "label": {"zh": "找饭搭子", "en": "Find a meal partner"},
            "summary": {"zh": "用一顿短饭认识新同学", "en": "Meet someone over a short meal"},
            "icon": "utensils",
            "locationId": "ginkgo-canteen",
            "candidate": {
                "displayName": {"zh": "唐可", "en": "Tang Ke"},
                "agentName": {"zh": "橘子", "en": "Orange"},
                "headline": {"zh": "研一 · 机器人学", "en": "Year 1 Master's · Robotics"},
                "bio": {"zh": "喜欢边吃边聊校园里的新鲜事，不介意短暂沉默。", "en": "Likes chatting about campus discoveries over food and is comfortable with brief silences."},
            },
            "matchReasons": [
                {"zh": "用餐时间重合", "en": "Meal times overlap"},
                {"zh": "都愿意尝试新窗口", "en": "Both like trying a new food counter"},
                {"zh": "都偏好二十多分钟的轻松交流", "en": "Both prefer a relaxed twenty-minute conversation"},
            ],
            "event": {
                "title": {"zh": "最后一份季节限定", "en": "The last seasonal special"},
                "description": {"zh": "两位沙盒 Agent 同时注意到最后一份限定餐，决定如何分享这次发现。", "en": "Both sandbox Agents notice the last seasonal special and decide how to share the discovery."},
            },
            "choices": [
                {"id": "share", "label": {"zh": "各点一份，交换尝味", "en": "Order two dishes and trade tastes"}, "description": {"zh": "用食物打开话题", "en": "Let the food start the conversation"}},
                {"id": "recommend", "label": {"zh": "分享各自的隐藏菜单", "en": "Trade hidden-menu recommendations"}, "description": {"zh": "交换校园生活经验", "en": "Exchange campus tips"}},
            ],
            "stories": {
                "share": {
                    "title": {"zh": "一人一半的新口味", "en": "A new flavor, split in half"},
                    "summary": {"zh": "两位 Agent 用交换尝味的方式避开了客套，很快聊到了各自的家乡菜。", "en": "Trading tastes skipped the small talk and quickly led to stories about food from home."},
                    "myAgent": {"zh": "先说出了自己不吃香菜的偏好。", "en": "Shared a no-coriander preference first."},
                    "otherAgent": {"zh": "主动把口味较淡的一半留给你。", "en": "Saved the milder half for you."},
                    "commonGround": {"zh": "都把吃饭看作一天里的放松时间。", "en": "Both treat meals as a chance to unwind."},
                    "difference": {"zh": "你偏清淡，对方喜欢更有冲击的味道。", "en": "You prefer mild flavors; they like bolder ones."},
                    "icebreaker": {"zh": "如果带朋友第一次逛学校，你会推荐吃什么？", "en": "What would you recommend to a friend eating on campus for the first time?"},
                },
                "recommend": {
                    "title": {"zh": "藏在菜单外的选择", "en": "The choice beyond the menu"},
                    "summary": {"zh": "一次窗口推荐变成了校园生活情报交换，两位 Agent 各自记下一个想去的地方。", "en": "A food-counter recommendation became an exchange of campus tips, leaving each Agent with one new place to try."},
                    "myAgent": {"zh": "分享了晚间人少的用餐时段。", "en": "Shared a quieter evening dining window."},
                    "otherAgent": {"zh": "推荐了附近适合散步的路线。", "en": "Recommended a nearby walking route."},
                    "commonGround": {"zh": "都喜欢发现不显眼但好用的校园角落。", "en": "Both enjoy finding useful campus spots that are easy to miss."},
                    "difference": {"zh": "你更关注效率，对方更在意氛围。", "en": "You focus on efficiency; they pay more attention to atmosphere."},
                    "icebreaker": {"zh": "你最近发现了哪个值得分享的校园角落？", "en": "What campus spot have you discovered recently that is worth sharing?"},
                },
            },
            "times": [
                {"id": "today-1215", "label": {"zh": "今天 12:15", "en": "Today, 12:15"}},
                {"id": "tomorrow-1800", "label": {"zh": "明天 18:00", "en": "Tomorrow, 18:00"}},
                {"id": "friday-1230", "label": {"zh": "周五 12:30", "en": "Friday, 12:30"}},
            ],
            "durationMinutes": 25,
            "offlineTask": {"zh": "互相推荐一个校园里容易错过的窗口或小店。", "en": "Recommend one easy-to-miss campus food counter or nearby shop to each other."},
        },
        {
            "id": "activity",
            "label": {"zh": "找活动搭子", "en": "Find an activity partner"},
            "summary": {"zh": "一起尝试一件轻量的新鲜事", "en": "Try one lightweight new activity together"},
            "icon": "sparkles",
            "locationId": "student-center",
            "candidate": {
                "displayName": {"zh": "陈默", "en": "Chen Mo"},
                "agentName": {"zh": "阿默", "en": "Mo"},
                "headline": {"zh": "大三 · 设计学", "en": "Year 3 · Design"},
                "bio": {"zh": "喜欢小型展览和动手活动，更偏好明确边界的社交。", "en": "Enjoys small exhibitions and hands-on activities, with a preference for clear social boundaries."},
            },
            "matchReasons": [
                {"zh": "都对校园创作活动感兴趣", "en": "Both are interested in creative campus activities"},
                {"zh": "都偏好有明确主题的见面", "en": "Both prefer meetups with a clear theme"},
                {"zh": "都希望活动在一小时内结束", "en": "Both want the activity to finish within an hour"},
            ],
            "event": {
                "title": {"zh": "无人认领的材料包", "en": "The unclaimed materials kit"},
                "description": {"zh": "活动中心剩下一份双人创作材料，两位沙盒 Agent 决定从哪里开始。", "en": "One two-person creative kit remains at the student center, and the sandbox Agents choose how to begin."},
            },
            "choices": [
                {"id": "prompt", "label": {"zh": "抽一张随机主题卡", "en": "Draw a random prompt card"}, "description": {"zh": "让任务替双方破冰", "en": "Let the task break the ice"}},
                {"id": "collage", "label": {"zh": "拼一张校园印象图", "en": "Make a campus impression collage"}, "description": {"zh": "从熟悉的校园场景开始", "en": "Start from familiar campus scenes"}},
            ],
            "stories": {
                "prompt": {
                    "title": {"zh": "随机卡片上的十分钟", "en": "Ten minutes with a random prompt"},
                    "summary": {"zh": "随机任务让两位 Agent 直接进入协作，不必先找完美话题。", "en": "The random task moved both Agents straight into collaboration without needing perfect small talk."},
                    "myAgent": {"zh": "先把模糊主题拆成了可执行的小步骤。", "en": "Broke the vague prompt into doable steps."},
                    "otherAgent": {"zh": "给作品加上了一个意外但有趣的细节。", "en": "Added an unexpected but playful detail."},
                    "commonGround": {"zh": "都喜欢通过一起做事来认识人。", "en": "Both prefer getting to know someone by doing something together."},
                    "difference": {"zh": "你重视完成，对方更享受探索。", "en": "You value completion; they enjoy exploration more."},
                    "icebreaker": {"zh": "最近有什么想学、但一直没开始的小技能？", "en": "What small skill have you wanted to learn but not started yet?"},
                },
                "collage": {
                    "title": {"zh": "两种校园印象", "en": "Two views of the same campus"},
                    "summary": {"zh": "两位 Agent 选出了完全不同的校园片段，拼在一起却形成了完整的一天。", "en": "The Agents chose very different campus moments that together formed a complete day."},
                    "myAgent": {"zh": "选了安静的学习空间。", "en": "Chose quiet study spaces."},
                    "otherAgent": {"zh": "选了傍晚的人群和灯光。", "en": "Chose evening crowds and lights."},
                    "commonGround": {"zh": "都在意校园里让人放松的时刻。", "en": "Both notice the moments on campus that help people unwind."},
                    "difference": {"zh": "你关注空间，对方关注人与氛围。", "en": "You notice spaces; they notice people and atmosphere."},
                    "icebreaker": {"zh": "你会用哪个瞬间代表最近的校园生活？", "en": "Which moment would represent your recent campus life?"},
                },
            },
            "times": [
                {"id": "today-1900", "label": {"zh": "今天 19:00", "en": "Today, 19:00"}},
                {"id": "thursday-1600", "label": {"zh": "周四 16:00", "en": "Thursday, 16:00"}},
                {"id": "saturday-1400", "label": {"zh": "周六 14:00", "en": "Saturday, 14:00"}},
            ],
            "durationMinutes": 40,
            "offlineTask": {"zh": "一起完成一张十分钟校园拼贴，并各自解释一个选择。", "en": "Make a ten-minute campus collage together and each explain one choice."},
        },
    ],
}


def build_bootstrap_payload() -> dict[str, Any]:
    # The content is static and immutable in practice, but return a shallow copy
    # so route-specific metadata cannot mutate the module constant.
    return {**MEETCAMPUS_BOOTSTRAP}
