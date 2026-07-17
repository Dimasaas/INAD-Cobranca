"""
add_pdf_importer.py — Compilador do painel INAD

Lê inad_template.html e gera inad_whatsapp.html injetando os dados de
clientes do arquivo clients_data.json no placeholder CLIENTS_JSON_PLACEHOLDER.

Se clients_data.json não existir, gera o HTML com uma base vazia ({}).
Isso garante que o build do CI/CD no GitHub Actions funcione corretamente.

Uso:
  python3 add_pdf_importer.py
"""

import json
import sys
from pathlib import Path

BASE_DIR     = Path(__file__).parent.resolve()
CLIENTS_FILE = BASE_DIR / "clients_data.json"
TEMPLATE     = BASE_DIR / "inad_template.html"
OUTPUT       = BASE_DIR / "inad_whatsapp.html"

PLACEHOLDER  = "CLIENTS_JSON_PLACEHOLDER"

# Valores inválidos conhecidos de telefone no legado
BAD_CEL_VALUES = {"E-mail:", "351", "0", "-"}


def load_clients() -> dict:
    """Carrega clients_data.json. Retorna {} se o arquivo não existir."""
    if not CLIENTS_FILE.exists():
        print(f"[AVISO] {CLIENTS_FILE.name} não encontrado. Gerando HTML com base vazia.")
        return {}

    with CLIENTS_FILE.open("r", encoding="utf-8") as f:
        clients = json.load(f)

    # Limpa valores de telefone inválidos
    for c_data in clients.values():
        if c_data.get("cel", "") in BAD_CEL_VALUES:
            c_data["cel"] = ""

    return clients


def main() -> None:
    if not TEMPLATE.exists():
        print(f"[ERRO] Template não encontrado: {TEMPLATE}")
        print("       Execute este script a partir da pasta raiz do projeto.")
        sys.exit(1)

    clients    = load_clients()
    clients_js = json.dumps(clients, ensure_ascii=False, separators=(",", ":"))

    html_template = TEMPLATE.read_text(encoding="utf-8")

    if PLACEHOLDER not in html_template:
        print(f"[ERRO] Placeholder '{PLACEHOLDER}' não encontrado em {TEMPLATE.name}.")
        sys.exit(1)

    html_out = html_template.replace(PLACEHOLDER, clients_js)
    OUTPUT.write_text(html_out, encoding="utf-8")

    size_bytes = len(html_out.encode("utf-8"))
    size_kb    = size_bytes / 1024
    n_clients  = len(clients)

    print(f"[OK] {OUTPUT.name} gerado com sucesso!")
    print(f"     Tamanho  : {size_bytes:,} bytes ({size_kb:.1f} KB)")
    print(f"     Clientes : {n_clients} clientes embutidos no HTML")


if __name__ == "__main__":
    main()
