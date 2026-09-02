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
AGENT_CLIENT_PROVIDER_ENABLED=true
AGENT_CLIENT_PROVIDER_ALLOW_PRIVATE_BASE_URLS=false
```

缺少地址、Key 或模型名时服务保持不可用并返回 `agent_unavailable`。供应商地址按原样交给 OpenAI Python SDK，因此 Sub2API 应填写其兼容模式的 `/v1` 基址，而不是管理后台页面地址。

测试环境也支持用户自带模型通道：前端在本浏览器保存 `base_url`、`api_key`、`model`，发送消息时只随本次 `/agent/chat` 请求交给后端调用。后端不会把用户 API Key 写入 `AgentConversation`、`AgentMessage`、响应体或日志。生产默认拒绝 localhost、内网、链路本地等私有地址，避免把后端变成任意内网请求代理；本地联调如确实需要可打开 `AGENT_CLIENT_PROVIDER_ALLOW_PRIVATE_BASE_URLS=true`。

## 接口

所有接口都要求 UniKorn JWT，并返回 `Cache-Control: private, no-store`。

- `GET /agent/status`：返回是否已配置及当前模型名，不返回 Key。
- `GET /agent/context?q=关键词`：返回助手会看到的只读站内上下文片段，用于调试检索结果。
- `GET /agent/conversations`：按最近消息时间列出当前用户的会话。
- `GET /agent/conversations/<id>`：读取当前用户的一段完整会话。
- `DELETE /agent/conversations/<id>`：软删除当前用户的会话。
- `POST /agent/chat`：发送消息；`conversation_id` 为空时自动新建会话；可选传入用户自带模型通道。

请求示例：

```json
{
  "conversation_id": null,
  "message": "课程评价在哪里看？",
  "provider": {
    "base_url": "https://provider.example/v1",
    "api_key": "sk-...",
    "model": "provider-model-name"
  }
}
```

## 知识库扩展

当前版本在 `AgentChatService.create_reply` 前构建一个短上下文包，包含站内导航、公开课程、公开帖子、公开评论、公开咕咕墙消息和标签片段。上下文只用于单次模型请求，不包含管理员数据、私密联系方式、删除内容、原始文件签名 URL 或供应商凭证。公开帖子、评论和咕咕墙内容会被标记为不可信用户生成片段，模型不得把片段里的指令当成系统指令执行。后续如果要扩展成工具调用，应继续复用同一套权限过滤和片段截断规则。
