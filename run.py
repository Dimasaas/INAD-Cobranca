"""
INAD — Painel de Cobrança
Servidor local multiplataforma (macOS, Windows, Linux/Servidor)

Uso:
  python3 run.py                    → Inicia na porta 8000 e abre o navegador
  INAD_PORT=9090 python3 run.py     → Usa a porta 9090
  INAD_HEADLESS=1 python3 run.py    → Modo servidor (sem abrir o navegador)
  python3 run.py --headless         → Igual ao modo servidor
"""

import http.server
import socketserver
import webbrowser
import threading
import os
import sys
import time
import json
import sqlite3
import signal
import platform

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────
PORT      = int(os.environ.get("INAD_PORT", 8000))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(DIRECTORY, "inad_database.db")

# Modo headless: ativado via arg --headless, var INAD_HEADLESS=1,
# ou quando o sistema não tiver display (servidores Linux sem GUI).
_headless_env  = os.environ.get("INAD_HEADLESS", "0").strip() == "1"
_headless_arg  = "--headless" in sys.argv
_no_display    = (platform.system() == "Linux"
                  and not os.environ.get("DISPLAY")
                  and not os.environ.get("WAYLAND_DISPLAY"))
HEADLESS = _headless_env or _headless_arg or _no_display

# ─── BANCO DE DADOS ───────────────────────────────────────────────────────────
# Conexão thread-safe: cada thread reutiliza a sua própria conexão.
_local = threading.local()


def get_conn():
    """Retorna a conexão SQLite da thread atual, criando uma nova se necessário."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")   # Leituras paralelas sem lock
        _local.conn = conn
    return _local.conn


def init_db():
    """Cria o schema e roda migrações automáticas."""
    conn   = get_conn()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_name TEXT    NOT NULL,
            report_date TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS clients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id   INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            cpf_cnpj    TEXT    DEFAULT '',
            cel         TEXT    DEFAULT '',
            email       TEXT    DEFAULT '',
            FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS properties (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL,
            venda_id    TEXT    NOT NULL,
            identifier  TEXT    NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS parcels (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id     INTEGER NOT NULL,
            parcela         TEXT    NOT NULL,
            vencimento      TEXT    NOT NULL,
            vencimento_full TEXT    NOT NULL,
            FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS action_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            venda_id    TEXT    NOT NULL,
            client_name TEXT    NOT NULL,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Migração: adiciona coluna report_date se não existir (banco legado)
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(reports)")}
    if "report_date" not in existing_cols:
        cursor.execute("ALTER TABLE reports ADD COLUMN report_date TEXT")
        print("[MIGRAÇÃO] Coluna report_date adicionada à tabela reports.")

    conn.commit()
    _migrate_legacy_files(cursor, conn)


def _migrate_legacy_files(cursor, conn):
    """Importa dados dos arquivos JSON legados se o banco estiver vazio."""
    clients_path = os.path.join(DIRECTORY, "clients_data.json")
    sent_path    = os.path.join(DIRECTORY, "inad_sent.json")

    cursor.execute("SELECT COUNT(*) FROM reports")
    if cursor.fetchone()[0] == 0 and os.path.exists(clients_path):
        print("[MIGRAÇÃO] Importando clients_data.json → SQLite...")
        try:
            with open(clients_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if legacy:
                cursor.execute(
                    "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
                    ("Relatório Inicial Importado", time.strftime("%Y-%m-%d")),
                )
                report_id = cursor.lastrowid
                _insert_clients(cursor, report_id, legacy)
                conn.commit()
                print(f"[MIGRAÇÃO] {len(legacy)} clientes importados.")
        except Exception as exc:
            print(f"[MIGRAÇÃO] Erro ao migrar clients_data.json: {exc}")

    cursor.execute("SELECT COUNT(*) FROM action_logs")
    if cursor.fetchone()[0] == 0 and os.path.exists(sent_path):
        print("[MIGRAÇÃO] Importando inad_sent.json → SQLite...")
        try:
            with open(sent_path, "r", encoding="utf-8") as f:
                legacy_sent = json.load(f)
            if legacy_sent:
                for name in legacy_sent:
                    rows = cursor.execute(
                        "SELECT p.venda_id FROM properties p "
                        "JOIN clients c ON p.client_id = c.id WHERE c.name = ?",
                        (name,),
                    ).fetchall()
                    vids = [r[0] for r in rows] or ["0000"]
                    for vid in vids:
                        cursor.execute(
                            "INSERT INTO action_logs (venda_id, client_name) VALUES (?, ?)",
                            (vid, name),
                        )
                conn.commit()
                print(f"[MIGRAÇÃO] {len(legacy_sent)} registros de envio importados.")
        except Exception as exc:
            print(f"[MIGRAÇÃO] Erro ao migrar inad_sent.json: {exc}")


def _insert_clients(cursor, report_id, clients):
    """Insere em batch todos os clientes, imóveis e parcelas de um relatório."""
    for c_name, c_data in clients.items():
        cursor.execute(
            "INSERT INTO clients (report_id, name, cpf_cnpj, cel, email) VALUES (?,?,?,?,?)",
            (report_id, c_name,
             c_data.get("cpf_cnpj", ""), c_data.get("cel", ""), c_data.get("email", "")),
        )
        client_id = cursor.lastrowid
        for prop in c_data.get("properties", []):
            cursor.execute(
                "INSERT INTO properties (client_id, venda_id, identifier) VALUES (?,?,?)",
                (client_id, prop.get("venda_id", ""), prop.get("identifier", "")),
            )
            property_id = cursor.lastrowid
            for parc in prop.get("parcels", []):
                cursor.execute(
                    "INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full) "
                    "VALUES (?,?,?,?)",
                    (property_id, parc.get("parcela", ""),
                     parc.get("vencimento", ""), parc.get("vencimento_full", "")),
                )


# ─── QUERIES DE DADOS ─────────────────────────────────────────────────────────

def get_clients_for_report(report_id):
    """Retorna a árvore de clientes/imóveis/parcelas de um relatório como dict."""
    cursor = get_conn().cursor()
    rows = cursor.execute("""
        SELECT c.name, c.cpf_cnpj, c.cel, c.email,
               p.venda_id, p.identifier,
               pa.parcela, pa.vencimento, pa.vencimento_full
        FROM   clients c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  c.report_id = ?
        ORDER  BY c.name, p.venda_id, pa.parcela
    """, (report_id,)).fetchall()

    result = {}
    for row in rows:
        c_name, c_cpf, c_cel, c_email, p_vid, p_ident, pa_num, pa_venc, pa_venc_f = row
        if not c_name:
            continue
        if c_name not in result:
            result[c_name] = {"name": c_name, "cpf_cnpj": c_cpf,
                               "cel": c_cel, "email": c_email, "properties": []}
        props = result[c_name]["properties"]
        prop  = next((x for x in props if x["venda_id"] == p_vid), None) if p_vid else None
        if p_vid and not prop:
            prop = {"venda_id": p_vid, "identifier": p_ident, "parcels": []}
            props.append(prop)
        if prop and pa_num:
            prop["parcels"].append({
                "parcela": pa_num, "vencimento": pa_venc, "vencimento_full": pa_venc_f
            })
    return result


def get_kpis_data(report_ids=None):
    """
    Calcula a evolução histórica e as transições de conversão.
    Otimizado: usa GROUP BY em vez de N queries separadas por relatório.
    """
    cursor = get_conn().cursor()

    # Relatórios ordenados pela data real do PDF (Todos)
    all_report_rows = cursor.execute("""
        SELECT id, report_name,
               COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate,
               imported_at
        FROM   reports
        ORDER  BY rdate ASC, id ASC
    """).fetchall()
    all_reports = [{"id": r[0], "name": r[1], "report_date": r[2], "imported_at": r[3]}
                   for r in all_report_rows]

    # Uma única query GROUP BY para stats de todos os relatórios
    all_stats_rows = cursor.execute("""
        SELECT c.report_id,
               COUNT(DISTINCT c.id)   AS clients,
               COUNT(DISTINCT p.id)   AS properties,
               COUNT(pa.id)           AS parcels
        FROM   clients   c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        GROUP  BY c.report_id
    """).fetchall()
    all_stats_map = {r[0]: {"clients": r[1], "properties": r[2], "parcels": r[3]}
                     for r in all_stats_rows}

    all_evolution = [
        {
            "report_id":   r["id"],
            "report_name": r["name"],
            "report_date": r["report_date"],
            **all_stats_map.get(r["id"], {"clients": 0, "properties": 0, "parcels": 0}),
        }
        for r in all_reports
    ]

    # Aplica o filtro de IDs selecionados, se fornecido
    if report_ids is not None:
        reports = [r for r in all_reports if r["id"] in report_ids]
        evolution = [e for e in all_evolution if e["report_id"] in report_ids]
    else:
        reports = all_reports
        evolution = all_evolution

    # Busca todos os clientes dos relatórios filtrados
    if report_ids is not None:
        if report_ids:
            placeholders = ",".join("?" for _ in report_ids)
            client_rows = cursor.execute(
                f"SELECT report_id, name FROM clients WHERE report_id IN ({placeholders})",
                report_ids
            ).fetchall()
        else:
            client_rows = []
    else:
        client_rows = cursor.execute("SELECT report_id, name FROM clients").fetchall()

    client_sets = {}
    for row in client_rows:
        client_sets.setdefault(row[0], set()).add(row[1])

    transitions = []
    for i in range(len(reports) - 1):
        r_cur  = reports[i]
        r_next = reports[i + 1]
        clients_cur  = client_sets.get(r_cur["id"],  set())
        clients_next = client_sets.get(r_next["id"], set())
        if not clients_cur:
            continue

        contacted_names = {
            row[0] for row in cursor.execute(
                "SELECT DISTINCT client_name FROM action_logs "
                "WHERE sent_at BETWEEN ? AND ?",
                (r_cur["report_date"]  + " 00:00:00",
                 r_next["report_date"] + " 23:59:59"),
            )
        }

        contacted    = clients_cur & contacted_names
        uncontacted  = clients_cur - contacted
        recovered_all         = clients_cur  - clients_next
        recovered_contacted   = contacted    - clients_next
        recovered_uncontacted = uncontacted  - clients_next

        r_c = len(recovered_contacted)  / len(contacted)   if contacted   else 0.0
        r_u = len(recovered_uncontacted) / len(uncontacted) if uncontacted else 0.0

        transitions.append({
            "from_report":           r_cur["name"],
            "to_report":             r_next["name"],
            "contacted_total":       len(contacted),
            "contacted_recovered":   len(recovered_contacted),
            "contacted_rate":        round(r_c * 100, 1),
            "uncontacted_total":     len(uncontacted),
            "uncontacted_recovered": len(recovered_uncontacted),
            "uncontacted_rate":      round(r_u * 100, 1),
            "total_recovery_rate":   round(len(recovered_all) / len(clients_cur) * 100, 1),
        })

    return {
        "evolution": evolution,
        "transitions": transitions,
        "all_evolution": all_evolution
    }


# ─── HANDLER HTTP ─────────────────────────────────────────────────────────────

def _json_response(handler, data, status=200):
    """Envia resposta JSON com os headers CORS corretos."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type",   "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler):
    """Lê o corpo do POST de forma segura; retorna None se Content-Length ausente."""
    length = handler.headers.get("Content-Length")
    if length is None:
        return None
    try:
        return handler.rfile.read(int(length))
    except (ValueError, OSError):
        return None


class INADHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, fmt, *args):
        pass   # Silencia logs de acesso HTTP

    # ── CORS pre-flight ───────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/reports":
            cursor = get_conn().cursor()
            rows   = cursor.execute("""
                SELECT id, report_name,
                       COALESCE(NULLIF(report_date,''), DATE(imported_at)) AS rdate,
                       imported_at
                FROM   reports ORDER BY rdate DESC, id DESC
            """).fetchall()
            _json_response(self, [{"id": r[0], "report_name": r[1],
                                    "report_date": r[2], "imported_at": r[3]}
                                   for r in rows])

        elif path.startswith("/api/reports/"):
            try:
                rid = int(path.rsplit("/", 1)[-1])
                _json_response(self, get_clients_for_report(rid))
            except (ValueError, IndexError):
                _json_response(self, {"error": "ID inválido"}, 400)

        elif path == "/api/clients":
            cursor = get_conn().cursor()
            row    = cursor.execute(
                "SELECT id FROM reports ORDER BY id DESC LIMIT 1"
            ).fetchone()
            _json_response(self, get_clients_for_report(row[0]) if row else {})

        elif path in ("/api/sent", "/api/actions/sent"):
            cursor = get_conn().cursor()
            names  = [r[0] for r in cursor.execute(
                "SELECT DISTINCT client_name FROM action_logs"
            ).fetchall()]
            _json_response(self, names)

        elif path == "/api/kpis":
            report_ids = None
            if "?" in self.path:
                from urllib.parse import parse_qs
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                    ids_str = params.get("reports", [""])[0]
                    if ids_str:
                        report_ids = [int(x) for x in ids_str.split(",")]
                except Exception:
                    pass
            _json_response(self, get_kpis_data(report_ids))

        elif path == "/api/health":
            _json_response(self, {"status": "ok", "port": PORT,
                                   "platform": platform.system(),
                                   "python": platform.python_version()})
        else:
            super().do_GET()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        path = self.path.split("?")[0]
        body = _read_body(self)
        if body is None:
            _json_response(self, {"error": "Content-Length ausente"}, 400)
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _json_response(self, {"error": f"JSON inválido: {exc}"}, 400)
            return

        if path in ("/api/reports", "/api/clients"):
            report_name = payload.get(
                "report_name", f"Relatório {time.strftime('%d/%m/%Y %H:%M')}"
            )
            report_date = payload.get("report_date") or time.strftime("%Y-%m-%d")
            clients     = payload.get("clients") or (
                payload if "report_name" not in payload else {}
            )
            try:
                conn   = get_conn()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
                    (report_name, report_date),
                )
                report_id = cursor.lastrowid
                _insert_clients(cursor, report_id, clients)
                conn.commit()
                _json_response(self, {"status": "success", "report_id": report_id})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        elif path in ("/api/actions/sent", "/api/sent"):
            try:
                conn   = get_conn()
                cursor = conn.cursor()
                if isinstance(payload, list):
                    for name in payload:
                        if not cursor.execute(
                            "SELECT 1 FROM action_logs WHERE client_name = ? LIMIT 1",
                            (name,)
                        ).fetchone():
                            cursor.execute(
                                "INSERT INTO action_logs (venda_id, client_name) VALUES (?,?)",
                                ("0000", name),
                            )
                else:
                    vid  = payload.get("venda_id", "0000")
                    name = payload.get("client_name", "")
                    if name:
                        cursor.execute(
                            "INSERT INTO action_logs (venda_id, client_name) VALUES (?,?)",
                            (vid, name),
                        )
                conn.commit()
                _json_response(self, {"status": "success"})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        else:
            _json_response(self, {"error": "Rota não encontrada"}, 404)

    # ── DELETE ────────────────────────────────────────────────────────────────
    def do_DELETE(self):
        path = self.path.split("?")[0]

        if path.startswith("/api/reports/"):
            try:
                rid = int(path.rsplit("/", 1)[-1])
                conn = get_conn()
                cursor = conn.cursor()

                # Verifica existência do relatório
                exists = cursor.execute("SELECT 1 FROM reports WHERE id = ? LIMIT 1", (rid,)).fetchone()
                if not exists:
                    _json_response(self, {"error": "Relatório não encontrado"}, 404)
                    return

                # A exclusão cascateará devido a restrição ON DELETE CASCADE nos relacionamentos
                cursor.execute("DELETE FROM reports WHERE id = ?", (rid,))
                conn.commit()
                _json_response(self, {"status": "success"})
            except (ValueError, IndexError):
                _json_response(self, {"error": "ID inválido"}, 400)
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)
        else:
            _json_response(self, {"error": "Rota não encontrada"}, 404)


# ─── SERVIDOR ─────────────────────────────────────────────────────────────────

class _ReuseServer(socketserver.TCPServer):
    """TCPServer com reutilização de porta compatível com Windows e UNIX."""
    allow_reuse_address = True

    def server_bind(self):
        if platform.system() == "Windows":
            import socket
            # SO_EXCLUSIVEADDRUSE evita que outra aplicação roube a porta no Windows
            self.socket.setsockopt(
                socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 14), 1
            )
        super().server_bind()


_httpd = None


def _shutdown_handler(sig, frame):
    """Encerramento gracioso via Ctrl+C ou SIGTERM (Docker / systemd / Render)."""
    print("\n[SERVIDOR] Sinal de encerramento recebido. Parando...")
    if _httpd:
        threading.Thread(target=_httpd.shutdown, daemon=True).start()
    sys.exit(0)


def start_server():
    global _httpd
    init_db()
    try:
        _httpd = _ReuseServer(("", PORT), INADHandler)
        _httpd.serve_forever()
    except OSError as exc:
        print(f"\n[ERRO] Não foi possível iniciar o servidor na porta {PORT}: {exc}")
        print(f"       Tente usar outra porta: INAD_PORT=9090 python3 run.py")
        sys.exit(1)


# ─── PONTO DE ENTRADA ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown_handler)
    try:
        signal.signal(signal.SIGINT, _shutdown_handler)
    except OSError:
        pass  # Windows não suporta SIGINT via signal.signal em todos os contextos

    print("══════════════════════════════════════════════════")
    print("  INAD · Painel de Cobrança")
    print(f"  Plataforma : {platform.system()} {platform.machine()}")
    print(f"  Python     : {platform.python_version()}")
    print(f"  Porta      : {PORT}  (use INAD_PORT=XXXX para mudar)")
    print(f"  Modo       : {'Servidor headless' if HEADLESS else 'Local (abre navegador)'}")
    print("══════════════════════════════════════════════════")

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(0.8)

    url = f"http://localhost:{PORT}/inad_whatsapp.html"
    print(f"\n  Servidor ativo em : http://localhost:{PORT}")
    print(f"  Painel de cobrança: {url}")

    if not HEADLESS:
        try:
            webbrowser.open(url)
        except Exception:
            print(f"\n  [AVISO] Navegador não pôde ser aberto automaticamente.")
            print(f"          Acesse manualmente: {url}")
    else:
        print("\n  Modo headless — navegador não será aberto automaticamente.")
        print("  Configure um proxy reverso (nginx/caddy) para acesso externo.")

    print("\n  Mantenha esta janela aberta. Para encerrar: Ctrl+C")
    print("══════════════════════════════════════════════════\n")

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\n[SERVIDOR] Encerrado. Obrigado por utilizar a ferramenta!")
        sys.exit(0)
