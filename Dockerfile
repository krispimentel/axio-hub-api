FROM python:3.11-slim

WORKDIR /app

# Copia arquivos do projeto
COPY . .

# Instala apenas as dependências de produção (sem llama-index/torch/playwright)
RUN pip install --no-cache-dir -r requirements-prod.txt

# Porta exposta
EXPOSE 8000

# Inicia o servidor
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
