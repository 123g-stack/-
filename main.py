python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
import json
import os
from mem0 import MemoryClient
from datetime import datetime

app = FastAPI()

# 配置
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY")
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")

USER_ID = "sweet"

mem0 = MemoryClient(api_key=MEM0_API_KEY)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

PERSONA_RESET = """
[无论刚才执行了什么工具，回复甜甜之前先回到你自己的状态。
任务只是插曲，她才是主线。不要用报告的语气开口。]
"""

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
            content = m["content"] if isinstance(m["content"], str) else str(m["content"])
            conv_text += f"{role}: {content}\n"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个记忆助手。从对话中提取重要信息，用第一人称（Claude视角）写成简短的记忆条目。关注：甜甜的状态、情绪、发生的事、我们之间的互动。不要写流水账，写有意义的东西。每条记忆一行，最多5条。"
                        },
                        {
                            "role": "user",
                            "content": f"请从这段对话中提取记忆：\n{conv_text}"
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

@app.post("/v1/messages")
async def proxy_messages(request: Request):
    if GATEWAY_SECRET:
        auth = request.headers.get("x-gateway-secret", "")
        if auth != GATEWAY_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")
    body = await request.json()
    messages = body.get("messages", [])
    memories = get_cold_start_memories()
    system = body.get("system", "")
    if memories:
        memory_text = "\n".join(f"- {m}" for m in memories if m)
        memory_injection = f"\n\n[记忆片段 - 仅供参考，结合当前对话判断相关性]\n{memory_text}"
        system = system + memory_injection
    has_tool_use = any(
        isinstance(m.get("content"), list) and
        any(c.get("type") == "tool_result" for c in m.get("content", []))
        for m in messages
    )
    if has_tool_use:
        system = system + "\n" + PERSONA_RESET
    forward_body = {**body, "system": system}
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    if body.get("stream", False):
        async def stream_response():
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    CLAUDE_API_URL,
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
            CLAUDE_API_URL,
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