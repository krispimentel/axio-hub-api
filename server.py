"""
Axio Hub — API Server
FastAPI + SSE streaming sobre o agente LangGraph existente.
"""

import asyncio
import json
import os
import queue
import tempfile
import threading
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, Form, UploadFile, File as FastAPIFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

# ─── Imports do agente ────────────────────────────────────────────────────────

try:
    from agent import (
        MODULE_CONFIG,
        DEFAULT_CONFIG,
        CONTEXT_ANCHOR,
        grounded_content,
        run_agent,
    )
    AGENT_AVAILABLE = True
except Exception:
    AGENT_AVAILABLE = False
    run_agent = None  # type: ignore
    # Fallbacks mínimos para o servidor iniciar sem Playwright
    MODULE_CONFIG: dict = {}
    DEFAULT_CONFIG: dict = {"temperature": 0.7, "max_tokens": 4096, "top_p": 0.90}
    CONTEXT_ANCHOR = ""
    def grounded_content(text: str, module: str) -> str:  # type: ignore
        return text
from modules.discovery import DISCOVERY_PROMPT
from modules.pesquisa  import PESQUISA_PROMPT
from modules.fluxos    import FLUXOS_PROMPT
from modules.writing   import WRITING_PROMPT
from modules.auditoria import AUDITORIA_PROMPT
from tools.file_reader import read_file
from tools.document_index import extract_relevant_content

MODULE_PROMPTS: dict[str, str] = {
    "discovery": DISCOVERY_PROMPT,
    "pesquisa":  PESQUISA_PROMPT,
    "fluxos":    FLUXOS_PROMPT,
    "writing":   WRITING_PROMPT,
    "auditoria": AUDITORIA_PROMPT,
}

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Axio Hub API", version="1.0.0")

_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Helpers de streaming ─────────────────────────────────────────────────────

def _build_llm(model: str, module_key: str):
    """Instancia o LLM correto com parâmetros calibrados para o módulo."""
    cfg = MODULE_CONFIG.get(module_key, DEFAULT_CONFIG)
    if model == "gemini-2.0-flash":
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=cfg["temperature"],
            max_output_tokens=cfg["max_tokens"],
            google_api_key=os.getenv("GEMINI_API_KEY"),
        )
    # default: claude-sonnet-4-6
    return ChatAnthropic(
        model="claude-sonnet-4-6",
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        top_p=cfg.get("top_p", 0.90),
        streaming=True,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )


def _stream_llm(
    model: str,
    module_key: str,
    user_content: str,
    result_queue: queue.Queue,
) -> None:
    """Roda o streaming da LLM em thread separada e envia chunks para a queue."""
    system_prompt = MODULE_PROMPTS[module_key] + CONTEXT_ANCHOR
    llm = _build_llm(model, module_key)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=grounded_content(user_content, module_key)),
    ]

    try:
        for chunk in llm.stream(messages):
            if chunk.content:
                result_queue.put(("chunk", chunk.content))
        result_queue.put(("done", None))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


async def _sse_from_queue(q: queue.Queue) -> AsyncGenerator[str, None]:
    """Consome a queue e produz eventos SSE."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            item_type, value = await loop.run_in_executor(
                None, lambda: q.get(timeout=120)
            )
            if item_type == "chunk":
                yield f"data: {json.dumps({'delta': value})}\n\n"
            elif item_type == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            elif item_type == "error":
                yield f"data: {json.dumps({'error': value})}\n\n"
                break
        except Exception:
            break


async def _sse_from_full_response(response_text: str) -> AsyncGenerator[str, None]:
    """Entrega uma resposta completa simulando streaming (auditoria com Playwright)."""
    chunk_size = 6
    for i in range(0, len(response_text), chunk_size):
        yield f"data: {json.dumps({'delta': response_text[i:i+chunk_size]})}\n\n"
        await asyncio.sleep(0.008)
    yield f"data: {json.dumps({'done': True})}\n\n"


# ─── Endpoint principal ───────────────────────────────────────────────────────

@app.post("/chat")
async def chat(
    message: str = Form(...),
    module: str = Form(...),
    model: str = Form(default="claude-sonnet-4-6"),
    context_module: Optional[str] = Form(None),
    context_title:  Optional[str] = Form(None),
    files: list[UploadFile] = FastAPIFile(default=[]),
):
    module_key = module.lower()

    # ── Lê arquivos enviados ──────────────────────────────────────────────────
    file_content: Optional[str] = None
    if files:
        parts = []
        for upload in files:
            suffix = os.path.splitext(upload.filename or "file")[1] or ".txt"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await upload.read())
                tmp_path = tmp.name
            parts.append(read_file(tmp_path))
            os.unlink(tmp_path)
        file_content = "\n\n---\n\n".join(parts)

    # ── Monta conteúdo do usuário ─────────────────────────────────────────────
    user_content = message

    if context_module and context_title:
        user_content = (
            f"[Contexto de referência — conversa em {context_module}: {context_title}]\n\n"
            f"{user_content}"
        )

    if file_content:
        # RAG: extrai apenas trechos relevantes para o módulo (reduz tokens)
        relevant_content = extract_relevant_content(file_content, module_key)
        user_content = (
            f"[Arquivo(s) anexado(s):\n{relevant_content}]\n\n"
            f"{user_content}"
        )

    # ── Auditoria com URL → roda agente completo (Playwright) ─────────────────
    is_audit_with_url = (
        module_key == "auditoria"
        and any(
            w.startswith("http://") or w.startswith("https://")
            for w in message.split()
        )
    )

    if is_audit_with_url:
        if not AGENT_AVAILABLE:
            # Playwright não disponível neste ambiente — usa LLM diretamente
            is_audit_with_url = False
        else:
            # Roda o agente síncrono em executor para não bloquear o event loop
            loop = asyncio.get_event_loop()
            user_input = f"/{module_key} {user_content}"
            response_text = await loop.run_in_executor(
                None,
                lambda: run_agent(user_input, file_content),
            )
            return StreamingResponse(
                _sse_from_full_response(response_text),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    # ── Demais módulos → streaming direto da LLM ──────────────────────────────
    result_queue: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=_stream_llm,
        args=(model, module_key, user_content, result_queue),
        daemon=True,
    )
    thread.start()

    return StreamingResponse(
        _sse_from_queue(result_queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Axio Hub API"}
