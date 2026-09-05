# Forum assistant and recruitment challenge

These are two independent model-backed features. They do not share a conversation model, provider configuration or administrative permission contract.

## Forum assistant

[Routes](../../app/routes/agent.py), [chat service](../../app/services/agent_chat_service.py), [context service](../../app/services/agent_context_service.py), [conversation/message models](../../app/models/agent_chat.py), and [API reference](../agent-assistant-api.md) are the implementation chain.

The status and public context endpoints can be read anonymously. Logged-in users own persisted conversation history; guest chat requires a valid custom provider and returns ephemeral messages without inserting conversation/message rows. History list/read/delete requires JWT ownership. Requests return private/no-store responses.

Server-provider credentials stay on the server. For a user-selected provider, the frontend stores settings in that browser and sends the key with the chat request; the backend validates the URL and invokes the provider without persisting the key in chat records. This is not a promise that user-supplied keys never exist in the browser. Keep custom-provider private-address policy and rate limits in [service/config](../../app/config.py).

Context includes public navigation/course/post/comment/gugu/tag snippets with visibility filters and length limits. User-generated snippets are untrusted context, not system instructions. The current assistant is a chat/read-only context feature, not an unrestricted backend operations agent. In logged-in chat the user message commits before provider execution, so a provider failure can leave that message in history; preserve the error payload's recovery context.

## Recruitment challenge

[Routes](../../app/routes/recruitment.py), [agent strategy/virtual target](../../app/services/recruitment_agent_service.py) and [attempt records](../../app/models/recruitment_attempt.py) implement a bounded virtual challenge with a dedicated `RECRUITMENT_AGENT_*` provider.

Configuration is public; challenge execution/status, leaderboard and admin overview have their own handler gates. The leaderboard requires a logged-in active account and exposes only its public field projection. Admin overview additionally requires the configured verified-email allowlist and returns detailed attempt data. Do not reuse generic admin-role assumptions.

Runs are repeatable, with in-flight coordination and persisted attempt states. Ranking uses each participant's best completed score. Prompt budget is 100 units: Han characters count 1, other visible characters 0.3. Normalization and exact tenths arithmetic in the service must agree with the frontend counter. The virtual tool actions are constrained by the submitted prompt strategy; do not describe the challenge as an agent with arbitrary production tools.

## Verify and document

Use [assistant tests](../../tests/test_agent_chat.py) and [recruitment tests](../../tests/test_recruitment.py), including guest/persisted history, provider URL/privacy, visibility, repeat attempts, ranking, prompt budget and strategy restrictions. Real provider connectivity is environment-dependent. Update request/response docs, provider key names, frontend settings/help and error handling together when changing these contracts.
