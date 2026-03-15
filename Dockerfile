# Usa a imagem oficial do Playwright — já tem Chromium + todas as dependências
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

# Copia arquivos do projeto
COPY . .

# Instala uv e as dependências Python
RUN pip install uv --quiet
RUN uv sync --no-dev

# Porta exposta
EXPOSE 8000

# Inicia o servidor
CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
