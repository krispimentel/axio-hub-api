import os
from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.anthropic import Anthropic
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# Modelo multilíngue pequeno — funciona bem com português
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

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


def _setup(llm_model: str = "claude-sonnet-4-6"):
    """Configura LlamaIndex para usar Claude + embeddings locais."""
    Settings.llm = Anthropic(model=llm_model)
    Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)
    Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)


def extract_relevant_content(text: str, module: str) -> str:
    """
    Indexa o documento e extrai apenas os trechos mais relevantes
    para o módulo solicitado.
    Retorna uma string com o conteúdo filtrado.
    """
    if not text or not text.strip():
        return ""

    # Documentos curtos (menos de 2000 chars) não precisam de indexação
    if len(text) < 2000:
        return text

    print("  Indexando documento com LlamaIndex...")
    _setup()

    doc = Document(text=text)
    index = VectorStoreIndex.from_documents([doc])

    query = MODULE_QUERIES.get(module, "Quais são as informações mais relevantes?")
    query_engine = index.as_query_engine(similarity_top_k=6)
    response = query_engine.query(query)

    print("  Indexação concluída. Trechos relevantes extraídos.\n")
    return str(response)
