import http.server
import socketserver
import webbrowser
import threading
import os
import sys
import time

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Serve from the directory where run.py is located
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Silence standard request logging to keep console clean,
        # but feel free to print errors or custom logs here
        pass

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("", PORT), QuietHandler) as httpd:
            print(f"[SERVIDOR] Rodando localmente em http://localhost:{PORT}")
            httpd.serve_forever()
    except Exception as e:
        print(f"[ERRO] Falha ao iniciar o servidor: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("==================================================")
    print("  INAD · Painel de Cobrança")
    print("==================================================")
    print("Iniciando a ferramenta localmente...")

    # Start server in a background daemon thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Wait a brief moment for the server to bind and start
    time.sleep(0.5)

    url = f"http://localhost:{PORT}/inad_whatsapp.html"
    print(f"Abrindo navegador em: {url}")
    
    try:
        # Open user's default browser
        webbrowser.open(url)
    except Exception as e:
        print(f"[AVISO] Não foi possível abrir o navegador automaticamente: {e}")
        print(f"Por favor, abra manualmente no seu navegador: {url}")

    print("\nFerramenta rodando com sucesso!")
    print("Mantenha esta janela aberta enquanto estiver utilizando a ferramenta.")
    print("Para encerrar, feche esta janela ou pressione Ctrl+C aqui.")
    print("==================================================")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SERVIDOR] Parando servidor local...")
        print("Encerrado com sucesso. Obrigado por utilizar a ferramenta!")
        sys.exit(0)
