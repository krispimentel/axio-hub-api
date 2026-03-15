"""
RAG leve para extração de conteúdo relevante de documentos.
Usa Google Embeddings (text-embedding-004) — sem PyTorch, sem dependências pesadas.
"""

import math
import os

from langchain_google_genai import GoogleGenerativeAIEmbeddings
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore

EMBED_MODEL = "models/text-embedding-004"

# Queries específicas por módulo — cada módulo busca o que é relevante para ele
MODULE_QUERIES = {
    "discovery": (
        "Quais são os requisitos de negócio, objetivos, problemas e dores descritos?"
    ),
    "pesquisa": (
        "Quais são os padrões de comportamento, dores, necessidades, objetivos, "
        "motivações, modelos mentais, perfil demográfico, nível digital, canal preferido "
        "e citações marcantes dos usuários entrevistados? "
        "Inclua também agrupamentos comportamentais ou perfis distintos identificados."
    ),
    "fluxos": (
        "Quais são os fluxos, jornadas do usuário, etapas e requisitos de "
        "usabilidade descritos?"
    ),
    "writing": (
        "Quais são os termos, categorias, status, labels e textos que precisam "
        "ser padronizados ou criados?"
    ),
    "auditoria": (
        "Quais são os problemas de UX, inconsistências visuais, erros de "
        "usabilidade e oportunidades de melhoria identificados?"
    ),
}

# Limite de chunks retornados (controla tokens enviados ao LLM)
TOP_K = 6
CHUNK_SIZE = 600
CHUNK_OVERLAP = 60


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def extract_relevant_content(text: str, module: str) -> str:
    """
    Extrai apenas os trechos mais relevantes do documento para o módulo solicitado.
    - Documentos curtos (< 2000 chars): retorna direto, sem embeddings.
    - Documentos longos: chunking + Google embeddings + cosine similarity.
    """
    if not text or not text.strip():
        return ""

    if len(text) < 2000:
        return text

    print("  [RAG] Indexando documento com Google Embeddings...")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_text(text)

    if not chunks:
        return text[:4000]

    embedder = GoogleGenerativeAIEmbeddings(
        model=EMBED_MODEL,
        google_api_key=os.getenv("GEMINI_API_KEY"),
    )

    query = MODULE_QUERIES.get(module, "Quais são as informações mais relevantes?")

    chunk_embeddings = embedder.embed_documents(chunks)
    query_embedding = embedder.embed_query(query)

    scored = [
        (i, _cosine_similarity(query_embedding, emb))
        for i, emb in enumerate(chunk_embeddings)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Retorna os top-k chunks em ordem original (preserva coerência textual)
    top_indices = sorted(i for i, _ in scored[:TOP_K])
    result = "\n\n".join(chunks[i] for i in top_indices)

    print(f"  [RAG] {len(chunks)} chunks → {len(top_indices)} relevantes extraídos.\n")
    return result
