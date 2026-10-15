"""Local-first Chinese copy editor with an opt-in server-side model adapter."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .rewriter import TONE_LABELS, build_instructions, rewrite_demo

ROOT = Path(__file__).parent
MAX_CHARS = 6000
MAX_BODY = 64 * 1024
app = FastAPI(title="说人话 · Humanizer-zh", docs_url=None, redoc_url=None)


class RewriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    tone: Literal["natural", "concise", "professional"] = "natural"
    engine: Literal["demo", "model"] = "demo"
    consent: bool = False

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入需要润色的文字")
        return value


def model_settings():
    return (
        os.getenv("HUMANIZER_API_BASE", "https://api.openai.com/v1").rstrip("/"),
        os.getenv("HUMANIZER_API_KEY", "").strip(),
        os.getenv("HUMANIZER_MODEL", "").strip(),
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Bound JSON before parsing, including chunked requests without Content-Length.
    if request.url.path == "/api/rewrite" and request.method == "POST":
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"detail": "请使用 application/json"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "请求过大，请减少文本长度"}, status_code=413)
        request._body = bytes(body)
        # Custom header + no CORS opt-in blocks cross-origin browser requests.
        if request.headers.get("x-humanizer-request") != "1":
            return JSONResponse({"detail": "缺少请求标识"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def config():
    base, key, model = model_settings()
    parsed = urlsplit(base)
    return {"model_available": bool(key and model), "provider_host": parsed.hostname if key and model else None, "max_chars": MAX_CHARS}


async def rewrite_with_model(text: str, tone: str) -> str:
    base, key, model = model_settings()
    if not key or not model:
        raise HTTPException(503, "模型尚未配置，请使用本地规则演示")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, "服务端 API 地址配置无效，需要不含凭据或查询参数的 HTTPS 地址")
    payload = {"model": model, "instructions": build_instructions(tone), "input": text, "max_output_tokens": 8192, "store": False}
    try:
        # No redirects: Authorization is only sent to the configured API endpoint.
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{base}/responses", headers={"Authorization": f"Bearer {key}"}, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "completed":
            raise ValueError("Incomplete model output")
        output = data.get("output")
        if not isinstance(output, list):
            raise ValueError("Invalid model output")
        paragraphs = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                raise ValueError("Invalid message content")
            paragraphs.extend(part["text"] for part in content if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
        result = "\n".join(paragraphs).strip()
        if not result or len(result) > MAX_CHARS * 3:
            raise ValueError("Invalid model output")
        return result
    except httpx.TimeoutException:
        raise HTTPException(504, "模型响应超时，原文已保留，请稍后重试") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        # Never return provider response bodies: they can echo text or credentials.
        raise HTTPException(502, "模型服务暂不可用或返回了不完整结果，原文已保留") from None


@app.post("/api/rewrite")
async def rewrite(body: RewriteRequest):
    if body.engine == "model":
        if not body.consent:
            raise HTTPException(400, "请先同意将原文发送至配置的模型服务")
        result = await rewrite_with_model(body.text, body.tone)
        changes = ["由模型按所选语气改写，请逐句核对事实和原意。"]
    else:
        result, changes = rewrite_demo(body.text, body.tone)
    return {"text": result, "engine": body.engine, "tone": TONE_LABELS[body.tone], "changes": changes, "original_length": len(body.text), "result_length": len(result)}


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
