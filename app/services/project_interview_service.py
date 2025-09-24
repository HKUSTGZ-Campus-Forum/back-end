import os
import json
import logging
import time
from openai import OpenAI
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configure detailed logging for AI requests
ai_logger = logging.getLogger('ai_requests')
ai_logger.setLevel(logging.INFO)
if not ai_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - AI_REQUEST - %(message)s')
    handler.setFormatter(formatter)
    ai_logger.addHandler(handler)

class ProjectInterviewService:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = "qwen-plus"

        # Platform context for better AI understanding
        self.platform_context = """
=== 平台背景 ===
这是一个校园论坛的队友匹配系统，专门帮助大学生寻找项目合作伙伴。

平台特点：
- 用户群体：主要是大学生，来自不同专业和年级
- 项目类型：学术研究、课程项目、竞赛参与、创新创业、社团活动等
- 合作目标：组建团队完成特定项目，通常有明确的时间节点
- 技能匹配：系统会根据技能、经验、兴趣等因素进行智能推荐

队友匹配的关键要素：
1. 项目清晰度：让其他同学快速理解项目内容和价值
2. 技能需求：大致明确需要哪些专业背景或技能的队友
3. 时间安排：项目周期、每周投入时间等实际考量
5. 成果预期：项目最终目标，对参与者的收获和价值

优质项目描述的特征：
- 有具体的问题或需求驱动，不是泛泛而谈
- 有大致的技术栈或实现方向，便于匹配对口技能
- 具有一定的创新性或实用性
"""

    def _log_ai_request(self, method: str, messages: List[Dict], start_time: float, response_text: str = None, error: str = None):
        """Log AI request details for debugging and monitoring"""
        duration = time.time() - start_time

        log_data = {
            'method': method,
            'model': self.model,
            'duration_ms': round(duration * 1000, 2),
            'timestamp': time.time(),
            'messages_count': len(messages),
            'system_prompt_length': len(messages[0]['content']) if messages else 0,
            'user_input_length': len(messages[-1]['content']) if len(messages) > 1 else 0
        }

        if response_text:
            log_data['response_length'] = len(response_text)
            log_data['response_preview'] = response_text[:200] + '...' if len(response_text) > 200 else response_text

        if error:
            log_data['error'] = str(error)

        ai_logger.info(f"AI_REQUEST: {json.dumps(log_data, ensure_ascii=False)}")

        # Also log to main logger for important events
        if error:
            logger.error(f"AI request failed - Method: {method}, Duration: {duration:.3f}s, Error: {error}")
        elif duration > 5:
            logger.warning(f"Slow AI request - Method: {method}, Duration: {duration:.3f}s")
        else:
            logger.info(f"AI request completed - Method: {method}, Duration: {duration:.3f}s")

    def start_interview(self, initial_description: str) -> Dict[str, Any]:
        """Start interview with initial description"""
        start_time = time.time()
        method = "start_interview"

        try:
            # Enhanced system prompt with platform context
            system_prompt = f"""{self.platform_context}

=== 你的任务 ===
你是这个校园队友匹配系统的项目顾问AI。基于用户的初始项目想法，提出第一个有价值的问题来帮助完善项目描述，使其更适合招募队友。

=== 重要指导原则 ===
1. 理解这是校园环境，用户是大学生，项目通常与学习、竞赛、创新创业相关
2. 问题要切中要害，帮助明确项目对队友的吸引力和价值
3. 选项要实用且符合大学生的实际情况和能力范围
4. 优先询问能显著影响队友匹配效果的关键信息

=== 输出要求 ===
1. 只提出一个问题，问题要具体且有助于项目规划
2. 提供3-4个选项供用户选择，选项要具体且实用
3. 不要在选项中包含"其他"或"跳过"选项
4. 根据项目特点智能选择问题重点：目标用户群体、技术难度、时间投入、协作方式等
5. 严格用JSON格式回答：{{"question": "问题内容", "options": ["选项1", "选项2", "选项3", "选项4"]}}

=== 优质问题示例 ===
- 技术类项目："这个项目的技术难度大概在什么级别？"
- 创意类项目："你希望这个项目的成果形式是什么？"
- 竞赛类项目："你打算投入多长时间来完成这个项目？"
- 服务类项目："这个项目主要解决校园里哪个群体的问题？"

请基于用户的项目想法，选择最合适的切入点提出问题。"""

            user_prompt = f"用户的项目想法：{initial_description}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # Log request start
            ai_logger.info(f"Starting {method} - Initial description length: {len(initial_description)}")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )

            # Parse JSON response
            response_text = response.choices[0].message.content.strip()

            # Log successful response
            self._log_ai_request(method, messages, start_time, response_text)

            # Try to extract JSON from response
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()

            try:
                interview_data = json.loads(response_text)
            except json.JSONDecodeError as json_error:
                # Log JSON parsing error
                logger.error(f"JSON parsing failed in {method}: {json_error}")
                logger.error(f"Raw response: {response_text}")
                raise Exception(f"Failed to parse AI response as JSON: {json_error}")

            # Validate response structure
            if not interview_data.get("question") or not interview_data.get("options"):
                raise Exception("AI response missing required 'question' or 'options' fields")

            if not isinstance(interview_data.get("options"), list) or len(interview_data["options"]) < 2:
                raise Exception("AI response 'options' must be a list with at least 2 items")

            logger.info(f"Interview started successfully - Question: {interview_data['question'][:100]}...")

            return {
                "success": True,
                "question": interview_data.get("question", ""),
                "options": interview_data.get("options", []),
                "round": 1
            }

        except Exception as e:
            # Log error
            self._log_ai_request(method, messages if 'messages' in locals() else [], start_time, error=str(e))
            logger.error(f"Error starting interview: {e}")
            return {
                "success": False,
                "message": f"Failed to start interview: {str(e)}"
            }

    def continue_interview(self, interview_history: List[Dict], round_number: int, initial_description: str = None) -> Dict[str, Any]:
        """Continue interview based on previous answers"""
        start_time = time.time()
        method = f"continue_interview_round_{round_number}"

        try:
            if round_number >= 5:  # Max 5 rounds
                return self.synthesize_description(interview_history, initial_description)

            # Build context from previous answers
            context = self._build_interview_context(interview_history, initial_description)

            # Enhanced system prompt with platform context and strategic question guidance
            system_prompt = f"""{self.platform_context}

=== 你的任务 ===
你是校园队友匹配系统的项目顾问AI。现在是第{round_number}轮对话，需要基于之前的对话内容提出下一个有价值的问题来进一步完善项目描述。

=== 轮次策略指导 ===
第2轮：重点关注项目的技术实现和技能需求 - 这直接影响队友匹配的准确性
第3轮：关注项目规模、时间安排和参与方式 - 帮助评估项目可行性
第4轮：探讨团队协作和成果预期 - 明确对参与者的价值和收获

=== 重要指导原则 ===
1. 每个问题都要与之前的问题不同，逐步深入
2. 结合用户的回答，智能调整问题重点
3. 问题要有助于提升项目对潜在队友的吸引力
4. 选项要反映大学生的真实情况和偏好

=== 输出要求 ===
1. 只提出一个问题，要与之前的问题不同，深入探讨项目细节
2. 提供3-4个选项供用户选择，选项要具体且实用
3. 不要在选项中包含"其他"或"跳过"选项
4. 根据项目特点和轮次智能选择问题重点
5. 严格用JSON格式回答：{{"question": "问题内容", "options": ["选项1", "选项2", "选项3", "选项4"]}}

=== 对话记录 ===
{context}

请分析用户的项目特点和之前的回答，提出最有价值的下一个问题。"""

            user_prompt = "请根据之前的对话内容提出下一个问题"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # Log request start
            ai_logger.info(f"Starting {method} - History entries: {len(interview_history)}")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )

            response_text = response.choices[0].message.content.strip()

            # Log successful response
            self._log_ai_request(method, messages, start_time, response_text)

            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()

            try:
                interview_data = json.loads(response_text)
            except json.JSONDecodeError as json_error:
                logger.error(f"JSON parsing failed in {method}: {json_error}")
                logger.error(f"Raw response: {response_text}")
                raise Exception(f"Failed to parse AI response as JSON: {json_error}")

            # Validate response structure
            if not interview_data.get("question") or not interview_data.get("options"):
                raise Exception("AI response missing required 'question' or 'options' fields")

            if not isinstance(interview_data.get("options"), list) or len(interview_data["options"]) < 2:
                raise Exception("AI response 'options' must be a list with at least 2 items")

            logger.info(f"Round {round_number} question generated - Question: {interview_data['question'][:100]}...")

            return {
                "success": True,
                "question": interview_data.get("question", ""),
                "options": interview_data.get("options", []),
                "round": round_number
            }

        except Exception as e:
            # Log error
            self._log_ai_request(method, messages if 'messages' in locals() else [], start_time, error=str(e))
            logger.error(f"Error continuing interview round {round_number}: {e}")
            return {
                "success": False,
                "message": f"Failed to continue interview: {str(e)}"
            }

    def synthesize_description(self, interview_history: List[Dict], initial_description: str = None) -> Dict[str, Any]:
        """Synthesize final comprehensive description from interview"""
        start_time = time.time()
        method = "synthesize_description"

        try:
            context = self._build_interview_context(interview_history, initial_description)

            # Enhanced system prompt with platform-specific context
            system_prompt = f"""{self.platform_context}

=== 你的任务 ===
你是校园队友匹配系统的项目顾问AI。基于用户的原始想法和多轮问答对话，生成一个完整、专业的项目描述，使其在队友匹配平台上更具吸引力。

=== 项目描述写作要求 ===
1. **核心要求**：必须保持用户最初项目想法的核心内容，这是项目的本质
2. **智能补充**：基于问答内容补充细节，重点突出：
   - 目标用户群体和解决的问题
   - 大致技术实现方向和所需技能
   - 对参与者的价值和收获
3. **队友导向**：描述要能够吸引合适的队友，明确表达项目的意义和参与价值
4. **校园适配**：语言风格适合大学生群体，体现学习和成长价值
5. **实用性**：描述要具体、可执行，避免空泛的表述

=== 格式和篇幅要求 ===
- 语言简洁专业，长度控制在300-500字
- 结构清晰：项目背景 → 解决方案 → 技术实现 → 团队需求 → 预期成果
- 只返回项目描述内容，不要额外说明或标题
- 使用段落分明的格式，便于阅读

=== 重要提醒 ===
生成的描述应该让读者明确知道：
1. 这是什么项目，要解决什么问题
2. 需要什么技能的队友
3. 参与这个项目能获得什么
生成的描述除了用于让其他用户阅读，还将被编码到embedding space用于语意检索和推荐；因此描述内容可以同时考虑为语意检索优化

=== 对话记录 ===
{context}"""

            user_prompt = "请生成最终的项目描述"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # Log request start
            ai_logger.info(f"Starting {method} - Interview history entries: {len(interview_history)}")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )

            synthesized_description = response.choices[0].message.content.strip()

            # Log successful response
            self._log_ai_request(method, messages, start_time, synthesized_description)

            # Validate description quality
            if len(synthesized_description) < 100:
                logger.warning(f"Generated description seems too short: {len(synthesized_description)} characters")
            elif len(synthesized_description) > 1000:
                logger.warning(f"Generated description seems too long: {len(synthesized_description)} characters")

            logger.info(f"Description synthesized successfully - Length: {len(synthesized_description)} characters")

            return {
                "success": True,
                "description": synthesized_description,
                "completed": True
            }

        except Exception as e:
            # Log error
            self._log_ai_request(method, messages if 'messages' in locals() else [], start_time, error=str(e))
            logger.error(f"Error synthesizing description: {e}")
            return {
                "success": False,
                "message": f"Failed to synthesize description: {str(e)}"
            }

    def _build_interview_context(self, interview_history: List[Dict], initial_description: str = None) -> str:
        """Build context string from interview history"""
        context = []

        # Add initial description at the beginning
        if initial_description:
            context.append("=== 用户最初的项目想法 ===")
            context.append(initial_description)
            context.append("")
            context.append("=== AI问答对话记录 ===")

        for i, entry in enumerate(interview_history, 1):
            if entry.get('question') and entry.get('answer'):
                context.append(f"第{i}轮 - 问题：{entry['question']}")
                context.append(f"用户回答：{entry['answer']}")
                context.append("")

        return "\n".join(context)

# Global service instance
project_interview_service = ProjectInterviewService()