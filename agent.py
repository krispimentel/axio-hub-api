from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv
import os
import requests
from bs4 import BeautifulSoup
import base64
import time

load_dotenv()

from modules.discovery import DISCOVERY_PROMPT
from modules.pesquisa import PESQUISA_PROMPT
from modules.fluxos import FLUXOS_PROMPT
from modules.writing import WRITING_PROMPT
from modules.auditoria import AUDITORIA_PROMPT
from tools.document_index import extract_relevant_content

# ─── Configuração por módulo ──────────────────────────────────────────────────
# temperature: 0.0–0.2 para módulos analíticos (menos divagação)
#              0.4–0.6 para módulos criativos (escrita, ideação)
# max_tokens:  dimensionado pelo tamanho esperado do entregável
# top_p:       0.85–0.95 — nucleus sampling mais restrito = menos off-topic
# stop:        sequências que sinalizam fim do entregável (evita conteúdo extra)

MODULE_CONFIG = {
    "discovery": dict(temperature=0.2, max_tokens=2048),
    "pesquisa":  dict(temperature=0.1, max_tokens=2048),
    "fluxos":    dict(temperature=0.2, max_tokens=2048),
    "writing":   dict(temperature=0.5, max_tokens=1536),
    "auditoria": dict(temperature=0.1, max_tokens=3072),
}

DEFAULT_CONFIG = dict(temperature=0.2, max_tokens=1536)

def get_llm(module: str) -> ChatAnthropic:
    """Retorna uma instância de LLM com parâmetros calibrados para o módulo."""
    cfg = MODULE_CONFIG.get(module, DEFAULT_CONFIG)
    return ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        **cfg,
    )

# LLM padrão (usado fora dos módulos)
llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=4096, api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─── Âncora de contexto ────────────────────────────────────────────────────────
# Sufixo adicionado ao system prompt de todo módulo.
# Instrui o modelo a não ir além do material fornecido.

CONTEXT_ANCHOR = """
---
REGRAS DE ESCOPO (inegociáveis):
- Responda APENAS com base no material fornecido pelo usuário.
- Se uma informação não estiver no contexto recebido, diga explicitamente "não há dados suficientes para analisar este ponto" — não invente, não suponha, não extrapole.
- Não adicione conselhos, sugestões ou análises além do que foi solicitado.
- Não repita informações que o usuário já forneceu como se fossem descobertas suas.
- Não encerre com frases genéricas de encerramento ("Espero ter ajudado", "Qualquer dúvida estou à disposição" etc.).
- O entregável termina quando o conteúdo solicitado estiver completo.
"""

# Prefixo adicionado à mensagem do usuário para reforçar o grounding
def grounded_content(content: str, module: str) -> str:
    """Envolve o conteúdo do usuário com instruções de grounding."""
    if not content.strip():
        return "Analise e gere o entregável com base no contexto acima."
    return (
        f"SOLICITAÇÃO DO USUÁRIO (responda estritamente dentro deste escopo):\n"
        f"{content}"
    )

# Verifica se Playwright está disponível
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ─── Estado do agente ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    user_input: str
    module: str
    content: str
    file_content: Optional[str]
    web_content: Optional[str]
    response: str

# ─── Roteador ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/discovery": "discovery",
    "/pesquisa":  "pesquisa",
    "/fluxos":    "fluxos",
    "/writing":   "writing",
    "/auditoria": "auditoria",
}

def router_node(state: AgentState) -> AgentState:
    """Identifica o módulo pelo comando /NomeModulo."""
    user_input = state["user_input"]
    lower = user_input.lower()

    for cmd, module in COMMANDS.items():
        if lower.startswith(cmd):
            content = user_input[len(cmd):].strip()
            return {**state, "module": module, "content": content}

    return {**state, "module": "unknown", "content": user_input}

def route_to_module(state: AgentState) -> str:
    return state["module"]

# ─── Ferramenta: leitura de site ─────────────────────────────────────────────

def fetch_website(url: str) -> str:
    """Baixa o conteúdo de um site e retorna o texto limpo."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:8000]
    except Exception as e:
        return f"Erro ao acessar o site: {e}"

# ─── Nó genérico para executar um módulo ─────────────────────────────────────

def run_module(system_prompt: str, state: AgentState) -> AgentState:
    module  = state.get("module", "")
    content = state["content"]

    # Contexto documental: apenas trechos relevantes via LlamaIndex
    if state.get("file_content"):
        relevant = extract_relevant_content(state["file_content"], module)
        content = f"Trechos relevantes do documento:\n{relevant}\n\n{content}"

    # Contexto web: HTML limpo da URL fornecida
    if state.get("web_content"):
        content = f"Conteúdo do site/produto:\n{state['web_content']}\n\n{content}"

    # Âncora de contexto: sufixo no system prompt que impede divagação
    anchored_system = system_prompt + CONTEXT_ANCHOR

    messages = [
        SystemMessage(content=anchored_system),
        HumanMessage(content=grounded_content(content, module)),
    ]

    # LLM calibrado para o módulo (temperature, top_p, max_tokens)
    module_llm = get_llm(module)
    response = module_llm.invoke(messages)
    return {**state, "response": response.content}

# ─── Ferramenta: navegação autônoma com Playwright ───────────────────────────

AUDIT_PAGES = [
    ("home",     "/"),
    ("cadastro", "/cadastro"),
    ("login",    "/login"),
    ("checkout", "/checkout"),
    ("produto",  "/produto"),
    ("carrinho", "/cart"),
    ("busca",    "/busca"),
]

def fetch_website_playwright(base_url: str) -> dict:
    """
    Navega pelo site com Playwright, captura screenshots das páginas
    mais relevantes para auditoria UX e retorna texto + imagens em base64.

    Retorna:
        {
          "pages": [{"label": str, "url": str, "text": str, "screenshot_b64": str}],
          "error": str | None
        }
    """
    results = []
    errors = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="pt-BR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            # ── Página principal ─────────────────────────────────────────────
            page = context.new_page()
            try:
                page.goto(base_url, wait_until="networkidle", timeout=20000)
                time.sleep(1)  # Aguarda animações

                # Screenshot da home completa
                screenshot_bytes = page.screenshot(full_page=True, type="png")
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

                # Texto limpo
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)[:6000]

                results.append({
                    "label": "home",
                    "url": base_url,
                    "text": text,
                    "screenshot_b64": screenshot_b64,
                })
            except Exception as e:
                errors.append(f"home: {e}")

            # ── Páginas secundárias (tenta, ignora 404) ──────────────────────
            secondary_pages = [
                ("cadastro", ["/cadastro", "/register", "/signup", "/criar-conta"]),
                ("login",    ["/login", "/entrar", "/signin"]),
                ("checkout", ["/checkout", "/finalizar-compra", "/pagamento"]),
                ("busca",    ["/busca", "/search", "/pesquisa"]),
            ]

            for label, candidates in secondary_pages:
                for path in candidates:
                    target = base_url.rstrip("/") + path
                    try:
                        resp = page.goto(target, wait_until="domcontentloaded", timeout=10000)
                        if resp and resp.status < 400:
                            time.sleep(0.5)
                            screenshot_bytes = page.screenshot(full_page=True, type="png")
                            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

                            html = page.content()
                            soup = BeautifulSoup(html, "html.parser")
                            for tag in soup(["script", "style"]):
                                tag.decompose()
                            text = soup.get_text(separator="\n", strip=True)[:4000]

                            results.append({
                                "label": label,
                                "url": target,
                                "text": text,
                                "screenshot_b64": screenshot_b64,
                            })
                            break  # Achou, não tenta os outros caminhos
                    except Exception:
                        continue

            browser.close()

    except Exception as e:
        return {"pages": results, "error": str(e)}

    return {"pages": results, "error": None if not errors else "; ".join(errors)}


# ─── Nós de cada módulo ──────────────────────────────────────────────────────

def discovery_node(state: AgentState) -> AgentState:
    return run_module(DISCOVERY_PROMPT, state)

def pesquisa_node(state: AgentState) -> AgentState:
    return run_module(PESQUISA_PROMPT, state)

def fluxos_node(state: AgentState) -> AgentState:
    return run_module(FLUXOS_PROMPT, state)

def writing_node(state: AgentState) -> AgentState:
    return run_module(WRITING_PROMPT, state)

def auditoria_node(state: AgentState) -> AgentState:
    """
    Módulo de auditoria — navegação autônoma com Playwright (visão + texto).
    Fallback para requests/BeautifulSoup se Playwright não estiver instalado.
    """
    content = state["content"]

    # Detecta URL no input
    url = None
    for word in content.split():
        if (word.startswith("http://") or word.startswith("https://")) and "figma.com" not in word:
            url = word
            break

    if not url:
        # Sem URL: auditoria baseada em documento/texto já no state
        return run_module(AUDITORIA_PROMPT, {**state, "web_content": None})

    # ── Playwright: navegação visual completa ─────────────────────────────────
    if PLAYWRIGHT_AVAILABLE:
        print(f"\n  Iniciando auditoria autônoma: {url}")
        print("  Navegando e capturando screenshots...")

        result = fetch_website_playwright(url)
        pages = result["pages"]

        if not pages:
            # Playwright falhou, fallback
            print("  Playwright falhou, usando fallback...")
            web_text = fetch_website(url)
            return run_module(AUDITORIA_PROMPT, {**state, "web_content": web_text})

        print(f"  {len(pages)} página(s) capturada(s): {[p['label'] for p in pages]}")

        # Monta contexto textual de todas as páginas
        web_text_parts = []
        for p in pages:
            web_text_parts.append(f"=== PÁGINA: {p['label'].upper()} ({p['url']}) ===\n{p['text']}")
        web_text = "\n\n".join(web_text_parts)

        # Monta conteúdo do usuário com screenshots (visão multimodal)
        user_content_parts = []

        # Documento anexado (prefixo, se houver)
        if state.get("file_content"):
            relevant = extract_relevant_content(state["file_content"], "auditoria")
            user_content_parts.append({
                "type": "text",
                "text": f"Trechos relevantes do documento anexado:\n{relevant}\n\n---\n\n",
            })

        # Solicitação principal com grounding explícito
        audit_request = content.replace(url, "").strip() or "Realize a auditoria UX completa."
        user_content_parts.append({
            "type": "text",
            "text": (
                f"SOLICITAÇÃO (responda estritamente dentro deste escopo):\n"
                f"{audit_request}\n\n"
                f"Conteúdo extraído de {len(pages)} página(s) do site:\n\n"
                f"{web_text}\n\n"
                "Analise também as screenshots capturadas a seguir:"
            ),
        })

        # Screenshots (máx. 4 para não estourar contexto)
        for p in pages[:4]:
            user_content_parts.append({
                "type": "text",
                "text": f"Screenshot — {p['label'].upper()} ({p['url']}):",
            })
            user_content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{p['screenshot_b64']}"},
            })

        messages = [
            SystemMessage(content=AUDITORIA_PROMPT + CONTEXT_ANCHOR),
            HumanMessage(content=user_content_parts),
        ]

        # LLM calibrado para auditoria (temperature=0.1, top_p=0.85)
        audit_llm = get_llm("auditoria")
        response = audit_llm.invoke(messages)
        return {**state, "web_content": web_text, "response": response.content}

    # ── Fallback: requests + BeautifulSoup ───────────────────────────────────
    print(f"\n  Acessando o site: {url}...")
    web_content = fetch_website(url)
    return run_module(AUDITORIA_PROMPT, {**state, "web_content": web_content})

def unknown_node(state: AgentState) -> AgentState:
    msg = (
        "Comando não reconhecido. Use um dos módulos disponíveis:\n\n"
        "  /Discovery  → Análise de requisitos e PRD\n"
        "  /Pesquisa   → Síntese de entrevistas\n"
        "  /Fluxos     → Criação e análise de fluxos\n"
        "  /Writing    → Microcopy e padronização\n"
        "  /Auditoria  → Auditoria UX completa\n\n"
        "Exemplo: /Discovery Preciso de um PRD para uma feature de notificações"
    )
    return {**state, "response": msg}

# ─── Construção do grafo ─────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("router",    router_node)
    graph.add_node("discovery", discovery_node)
    graph.add_node("pesquisa",  pesquisa_node)
    graph.add_node("fluxos",    fluxos_node)
    graph.add_node("writing",   writing_node)
    graph.add_node("auditoria", auditoria_node)
    graph.add_node("unknown",   unknown_node)

    graph.set_entry_point("router")

    graph.add_conditional_edges(
        "router",
        route_to_module,
        {
            "discovery": "discovery",
            "pesquisa":  "pesquisa",
            "fluxos":    "fluxos",
            "writing":   "writing",
            "auditoria": "auditoria",
            "unknown":   "unknown",
        },
    )

    for node in ["discovery", "pesquisa", "fluxos", "writing", "auditoria", "unknown"]:
        graph.add_edge(node, END)

    return graph.compile()

agent = build_graph()

# ─── Função pública ──────────────────────────────────────────────────────────

def run_agent(user_input: str, file_content: Optional[str] = None) -> str:
    result = agent.invoke({
        "user_input":   user_input,
        "module":       "",
        "content":      "",
        "file_content": file_content,
        "web_content":  None,
        "response":     "",
    })
    return result["response"]
