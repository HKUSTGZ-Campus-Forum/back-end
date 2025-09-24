import os
import json
import logging
from openai import OpenAI
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class ProjectInterviewService:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = "qwen-plus"

    def start_interview(self, initial_description: str) -> Dict[str, Any]:
        """Start interview with initial description"""
        try:
            system_prompt = """你是一个项目顾问，帮助学生完善项目描述。你的任务是基于用户的初始想法提出一个具体的问题来帮助他们细化项目。

规则：
1. 只提出一个问题，问题要具体且有助于项目规划
2. 提供3-4个选项供用户选择，选项要具体且实用
3. 不要在选项中包含"其他"或"跳过"选项
4. 问题应该围绍：目标用户、具体功能、技术实现、项目规模等方面
5. 用JSON格式回答：{"question": "问题内容", "options": ["选项1", "选项2", "选项3"]}

示例输出：
{"question": "这个项目主要服务于哪类用户？", "options": ["全校学生", "特定专业学生", "教师群体", "校外用户"]}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"用户的项目想法：{initial_description}"}
                ]
            )

            # Parse JSON response
            response_text = response.choices[0].message.content.strip()
            logger.info(f"LLM Response: {response_text}")

            # Try to extract JSON from response
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()

            interview_data = json.loads(response_text)

            return {
                "success": True,
                "question": interview_data.get("question", ""),
                "options": interview_data.get("options", []),
                "round": 1
            }

        except Exception as e:
            logger.error(f"Error starting interview: {e}")
            return {
                "success": False,
                "message": f"Failed to start interview: {str(e)}"
            }

    def continue_interview(self, interview_history: List[Dict], round_number: int) -> Dict[str, Any]:
        """Continue interview based on previous answers"""
        try:
            if round_number >= 5:  # Max 5 rounds
                return self.synthesize_description(interview_history)

            # Build context from previous answers
            context = self._build_interview_context(interview_history)

            system_prompt = f"""你是一个项目顾问，已经和用户进行了{round_number-1}轮对话。基于之前的对话内容，提出下一个有价值的问题来进一步完善项目描述。

规则：
1. 只提出一个问题，要与之前的问题不同，深入探讨项目细节
2. 提供3-4个选项供用户选择
3. 不要在选项中包含"其他"或"跳过"选项
4. 根据轮次重点询问：第2轮-技术实现，第3轮-项目规模，第4轮-推广计划
5. 用JSON格式回答：{{"question": "问题内容", "options": ["选项1", "选项2", "选项3"]}}

之前的对话内容：
{context}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请根据之前的对话内容提出下一个问题"}
                ]
            )

            response_text = response.choices[0].message.content.strip()
            logger.info(f"LLM Response Round {round_number}: {response_text}")

            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()

            interview_data = json.loads(response_text)

            return {
                "success": True,
                "question": interview_data.get("question", ""),
                "options": interview_data.get("options", []),
                "round": round_number
            }

        except Exception as e:
            logger.error(f"Error continuing interview: {e}")
            return {
                "success": False,
                "message": f"Failed to continue interview: {str(e)}"
            }

    def synthesize_description(self, interview_history: List[Dict]) -> Dict[str, Any]:
        """Synthesize final comprehensive description from interview"""
        try:
            context = self._build_interview_context(interview_history)

            system_prompt = """你是一个项目顾问，需要根据与用户的多轮对话，生成一个完整、专业的项目描述。

要求：
1. 综合所有对话内容，形成连贯的项目描述
2. 包含项目目标、目标用户、主要功能、技术实现思路
3. 描述要具体、可执行，适合招募队友
4. 语言简洁专业，长度控制在200-400字
5. 只返回项目描述内容，不要额外说明

对话记录：
{context}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请生成最终的项目描述"}
                ]
            )

            synthesized_description = response.choices[0].message.content.strip()
            logger.info(f"Synthesized description: {synthesized_description}")

            return {
                "success": True,
                "description": synthesized_description,
                "completed": True
            }

        except Exception as e:
            logger.error(f"Error synthesizing description: {e}")
            return {
                "success": False,
                "message": f"Failed to synthesize description: {str(e)}"
            }

    def _build_interview_context(self, interview_history: List[Dict]) -> str:
        """Build context string from interview history"""
        context = []
        for i, entry in enumerate(interview_history, 1):
            if entry.get('question') and entry.get('answer'):
                context.append(f"第{i}轮 - 问题：{entry['question']}")
                context.append(f"用户回答：{entry['answer']}")
                context.append("")

        return "\n".join(context)

# Global service instance
project_interview_service = ProjectInterviewService()