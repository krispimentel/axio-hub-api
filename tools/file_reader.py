import os

def read_file(file_path: str) -> str:
    """Lê arquivos PDF, DOCX, MD ou TXT e retorna o texto."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _read_pdf(file_path)
    elif ext in [".doc", ".docx"]:
        return _read_docx(file_path)
    elif ext in [".md", ".txt"]:
        return _read_text(file_path)
    else:
        return f"Formato de arquivo não suportado: {ext}"

def _read_pdf(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def _read_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs])

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
