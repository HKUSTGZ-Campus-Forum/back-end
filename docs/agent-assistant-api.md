# 论坛助手 API

第一版论坛助手提供 OpenAI 兼容模型调用和按登录用户隔离的对话历史。模型调用只发生在 Flask 后端，`AGENT_API_KEY` 不进入 API 响应、日志或前端构建。

## 配置

```dotenv
AGENT_ENABLED=true
AGENT_BASE_URL=https://provider.example/v1
AGENT_API_KEY=
AGENT_MODEL=provider-model-name
AGENT_TIMEOUT_SECONDS=60
AGENT_MAX_MESSAGE_CHARS=4000
AGENT_MAX_OUTPUT_TOKENS=800
AGENT_CONTEXT_MESSAGES=20
AGENT_REQUESTS_PER_MINUTE=10
```

缺少地址、Key 或模型名时服务保持不可用并返回 `agent_unavailable`。供应商地址按原样交给 OpenAI Python SDK，因此 Sub2API 应填写其兼容模式的 `/v1` 基址，而不是管理后台页面地址。

## 接口

所有接口都要求 UniKorn JWT，并返回 `Cache-Control: private, no-store`。

- `GET /agent/status`：返回是否已配置及当前模型名，不返回 Key。
- `GET /agent/conversations`：按最近消息时间列出当前用户的会话。
- `GET /agent/conversations/<id>`：读取当前用户的一段完整会话。
- `DELETE /agent/conversations/<id>`：软删除当前用户的会话。
- `POST /agent/chat`：发送消息；`conversation_id` 为空时自动新建会话。

请求示例：

```json
{
  "conversation_id": null,
  "message": "课程评价在哪里看？"
}
```

## 知识库扩展

当前版本只包含受控系统提示和有限的网站功能概览，主要用于验证供应商链路、聊天体验和历史记录。后续知识库应在 `AgentChatService.create_reply` 前增加检索步骤，将经过权限过滤、带来源标识的片段注入单次请求；原始私密内容、用户身份数据和供应商凭证不得写入向量库或提示缓存。
