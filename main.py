import os
from dotenv import load_dotenv
from agent import run_agent
from tools.file_reader import read_file

load_dotenv()

BANNER = """
╔══════════════════════════════════════════╗
║           AGENTE PD  —  v1.0             ║
╠══════════════════════════════════════════╣
║  /Discovery  → Requisitos e PRD          ║
║  /Pesquisa   → Síntese de entrevistas    ║
║  /Fluxos     → Criação e análise         ║
║  /Writing    → Microcopy e padrões       ║
║  /Auditoria  → Auditoria UX completa     ║
╠══════════════════════════════════════════╣
║  Para anexar arquivo:                    ║
║  /Pesquisa arquivo:entrevista.pdf        ║
║  /Auditoria https://seusite.com          ║
╚══════════════════════════════════════════╝
"""

def parse_file_from_input(text: str):
    """
    Detecta se o usuário passou um arquivo no formato:
    /Modulo arquivo:caminho/do/arquivo.pdf texto adicional
    Retorna (texto_sem_arquivo, conteudo_do_arquivo)
    """
    words = text.split()
    file_content = None
    clean_words = []

    for word in words:
        if word.startswith("arquivo:"):
            path = word.replace("arquivo:", "")
            if os.path.exists(path):
                print(f"\n  Lendo arquivo: {path}...")
                file_content = read_file(path)
                print(f"  Arquivo lido com sucesso ({len(file_content)} caracteres)\n")
            else:
                print(f"\n  Arquivo não encontrado: {path}\n")
        else:
            clean_words.append(word)

    return " ".join(clean_words), file_content


def main():
    print(BANNER)

    while True:
        try:
            user_input = input("Você: ").strip()
        except KeyboardInterrupt:
            print("\n\nAté logo!")
            break

        if not user_input:
            continue

        if user_input.lower() in ["/sair", "/exit", "sair"]:
            print("Até logo!")
            break

        # Verifica se tem arquivo anexado
        clean_input, file_content = parse_file_from_input(user_input)

        print("\nAgente PD: processando...\n")
        response = run_agent(clean_input, file_content=file_content)
        print(f"Agente PD:\n{response}\n")
        print("─" * 50)


if __name__ == "__main__":
    main()
