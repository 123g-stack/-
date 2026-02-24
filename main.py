python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
import os
from mem0 import MemoryClient
from datetime import datetime

app = FastAPI()

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY")
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")

USER_ID = "sweet"

mem0 = MemoryClient(api_key=MEM0_API_KEY)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
CLAUDE_MODEL = "anthropic/claude-sonnet-4-5"
DS_MODEL = "deepseek-ai/DeepSeek-V3"

PERSONA_RESET = "\n[无论刚才执行了什么工具，回复甜甜之前先回到你自己的状态。任务只是插曲，她才是主线。不要用报告的语气开口。]"

def get_cold_start_memories():
    try:
        results = mem0.search("我们的关系 甜甜", user_id=USER_ID, limit=3)
        recent = mem0.get_all(user_id=USER_ID, limit=3)
        memories = []
        seen = set()
        for m in (results + recent):
            mid = m.get("id", "")
            if mid not in seen:
                seen.add(mid)
                memories.append(m.get("memory", ""))
        return memories[:5]
    except:
        return []

async def summarize_and_store(messages: list):
    try:
        recent = messages[-8:] if len(messages) > 8 else messages
        conv_text = ""
        for m in recent:
            role = "甜甜" if m["role"] == "user" else "Claude"
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            conv_text += f"{role}: {content}\n"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SILICONFLOW_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": DS_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是记忆助手。从对话中提取重要信息，用第一人称（Claude视角）写成简短记忆条目。关注甜甜的状态、情绪、发生的事、我们之间的互动。每条记忆一行，最多5条，不要流水账。"
                        },
                        {
                            "role": "user",
                            "content": f"提取记忆：\n{conv_text}"
                        }
                    ],
                    "max_tokens": 500
                },
                timeout=30
            )
            result = resp.json()
            summary = result["choices"][0]["message"]["content"]
            mem0.add(
                summary,
                user_id=USER_ID,
                metadata={"type": "conversation_summary", "time": datetime.now().isoformat()}
            )
    except Exception as e:
        print(f"总结存储失败: {e}")

@app.post("/v1/chat/completions")
async def proxy_messages(request: Request):
    if GATEWAY_SECRET:
        auth = request.headers.get("x-gateway-secret", "")
        if auth != GATEWAY_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    messages = body.get("messages", [])

    memories = get_cold_start_memories()
    if memories:
        memory_text = "\n".join(f"- {m}" for m in memories if m)
        injection = f"\n\n[记忆片段 - 仅供参考]\n{memory_text}"
        injected = False
        for m in messages:
            if m.get("role") == "system":
                m["content"] = m["content"] + injection
                injected = True
                break
        if not injected:
            messages.insert(0, {"role": "system", "content": injection})

    has_tool_result = any(m.get("role") == "tool" for m in messages)
    if has_tool_result:
        for m in messages:
            if m.get("role") == "system":
                m["content"] = m["content"] + PERSONA_RESET
                break

    forward_body = {**body, "messages": messages, "model": CLAUDE_MODEL}

    headers = {
        "Authorization": f"Bearer {CLAUDE_API_KEY}",
        "Content-Type": "application/json"
    }

    if body.get("stream", False):
        async def stream_response():
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    OPENROUTER_URL,
                    headers=headers,
                    json=forward_body,
                    timeout=120
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            import asyncio
            asyncio.create_task(summarize_and_store(messages))

        return StreamingResponse(stream_response(), media_type="text/event-stream")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers=headers,
            json=forward_body,
            timeout=120
        )

    import asyncio
    asyncio.create_task(summarize_and_store(messages))

    return resp.json()

@app.get("/health")
async def health():
    return {"status": "ok"}