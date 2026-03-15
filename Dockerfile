FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema para o Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libcairo2 libatspi2.0-0 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

# Copia arquivos do projeto
COPY . .

# Instala uv e as dependências Python
RUN pip install uv --quiet
RUN uv sync --no-dev

# Instala apenas o Chromium do Playwright
RUN uv run playwright install chromium

# Porta exposta
EXPOSE 8000

# Inicia o servidor
CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
