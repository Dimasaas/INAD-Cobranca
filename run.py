import http.server
import socketserver
import webbrowser
import threading
import os
import sys
import time
import json
import sqlite3

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIRECTORY, "inad_database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Reports Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_name TEXT NOT NULL,
        report_date TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Run ALTER TABLE to add column if it was created in an older run
    try:
        cursor.execute("ALTER TABLE reports ADD COLUMN report_date TEXT")
    except sqlite3.OperationalError:
        pass
    
    # 2. Clients Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER,
        name TEXT NOT NULL,
        cpf_cnpj TEXT,
        cel TEXT,
        email TEXT,
        FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
    )
    """)
    
    # 3. Properties Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        venda_id TEXT NOT NULL,
        identifier TEXT NOT NULL,
        FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
    )
    """)
    
    # 4. Parcels Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS parcels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id INTEGER,
        parcela TEXT NOT NULL,
        vencimento TEXT NOT NULL,
        vencimento_full TEXT NOT NULL,
        FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
    )
    """)
    
    # 5. Action Logs Table (Sent logs)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS action_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venda_id TEXT NOT NULL,
        client_name TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    
    # Legacy data migration
    clients_json_path = os.path.join(DIRECTORY, "clients_data.json")
    sent_json_path = os.path.join(DIRECTORY, "inad_sent.json")
    
    cursor.execute("SELECT COUNT(*) FROM reports")
    report_count = cursor.fetchone()[0]
    
    if report_count == 0 and os.path.exists(clients_json_path):
        print("[MIGRAÇÃO] Importando dados de clientes legados de clients_data.json para o banco SQLite...")
        try:
            with open(clients_json_path, 'r', encoding='utf-8') as f:
                legacy_clients = json.load(f)
            
            if legacy_clients:
                cursor.execute("INSERT INTO reports (report_name, report_date) VALUES (?, ?)", ("Relatório Inicial Importado", time.strftime('%Y-%m-%d')))
                report_id = cursor.lastrowid
                
                for c_name, c_data in legacy_clients.items():
                    cursor.execute("""
                    INSERT INTO clients (report_id, name, cpf_cnpj, cel, email)
                    VALUES (?, ?, ?, ?, ?)
                    """, (report_id, c_name, c_data.get('cpf_cnpj', ''), c_data.get('cel', ''), c_data.get('email', '')))
                    client_id = cursor.lastrowid
                    
                    for prop in c_data.get('properties', []):
                        cursor.execute("""
                        INSERT INTO properties (client_id, venda_id, identifier)
                        VALUES (?, ?, ?)
                        """, (client_id, prop.get('venda_id', ''), prop.get('identifier', '')))
                        property_id = cursor.lastrowid
                        
                        for parc in prop.get('parcels', []):
                            cursor.execute("""
                            INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full)
                            VALUES (?, ?, ?, ?)
                            """, (property_id, parc.get('parcela', ''), parc.get('vencimento', ''), parc.get('vencimento_full', '')))
                
                print(f"[MIGRAÇÃO] Sucesso! Importados {len(legacy_clients)} clientes legados.")
        except Exception as e:
            print(f"[MIGRAÇÃO] Erro ao migrar clients_data.json: {e}")
            
    cursor.execute("SELECT COUNT(*) FROM action_logs")
    log_count = cursor.fetchone()[0]
    
    if log_count == 0 and os.path.exists(sent_json_path):
        print("[MIGRAÇÃO] Importando histórico de envios legado de inad_sent.json para o banco SQLite...")
        try:
            with open(sent_json_path, 'r', encoding='utf-8') as f:
                legacy_sent = json.load(f)
            
            if legacy_sent:
                for name in legacy_sent:
                    cursor.execute("""
                    SELECT p.venda_id FROM properties p
                    JOIN clients c ON p.client_id = c.id
                    WHERE c.name = ?
                    """, (name,))
                    venda_ids = [r[0] for r in cursor.fetchall()]
                    
                    if venda_ids:
                        for vid in venda_ids:
                            cursor.execute("""
                            INSERT INTO action_logs (venda_id, client_name)
                            VALUES (?, ?)
                            """, (vid, name))
                    else:
                        cursor.execute("""
                        INSERT INTO action_logs (venda_id, client_name)
                        VALUES (?, ?)
                        """, ("0000", name))
                print(f"[MIGRAÇÃO] Sucesso! Importados {len(legacy_sent)} registros de envio.")
        except Exception as e:
            print(f"[MIGRAÇÃO] Erro ao migrar inad_sent.json: {e}")
            
    conn.commit()
    conn.close()

def get_clients_for_report(report_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT c.id, c.name, c.cpf_cnpj, c.cel, c.email,
           p.id, p.venda_id, p.identifier,
           pa.parcela, pa.vencimento, pa.vencimento_full
    FROM clients c
    LEFT JOIN properties p ON p.client_id = c.id
    LEFT JOIN parcels pa ON pa.property_id = p.id
    WHERE c.report_id = ?
    """, (report_id,))
    rows = cursor.fetchall()
    conn.close()
    
    clients_dict = {}
    for row in rows:
        c_id, c_name, c_cpf, c_cel, c_email, p_id, p_vid, p_ident, pa_num, pa_venc, pa_venc_full = row
        if not c_name:
            continue
            
        if c_name not in clients_dict:
            clients_dict[c_name] = {
                "name": c_name,
                "cpf_cnpj": c_cpf,
                "cel": c_cel,
                "email": c_email,
                "properties": []
            }
            
        props = clients_dict[c_name]["properties"]
        prop = next((x for x in props if x["venda_id"] == p_vid), None) if p_vid else None
        if p_vid and not prop:
            prop = {
                "venda_id": p_vid,
                "identifier": p_ident,
                "parcels": []
            }
            props.append(prop)
            
        if prop and pa_num:
            prop["parcels"].append({
                "parcela": pa_num,
                "vencimento": pa_venc,
                "vencimento_full": pa_venc_full
            })
            
    return clients_dict

def get_kpis_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, report_name, report_date, imported_at FROM reports ORDER BY report_date ASC, id ASC")
    reports = []
    for r in cursor.fetchall():
        r_id, r_name, r_date, r_imported = r
        # Fallback to imported_at date if report_date is empty
        if not r_date:
            r_date = r_imported.split(" ")[0] if r_imported else time.strftime('%Y-%m-%d')
        reports.append({"id": r_id, "name": r_name, "report_date": r_date, "imported_at": r_imported})
    
    evolution = []
    for r in reports:
        cursor.execute("SELECT COUNT(DISTINCT id) FROM clients WHERE report_id = ?", (r["id"],))
        c_count = cursor.fetchone()[0]
        cursor.execute("""
        SELECT COUNT(pa.id) FROM parcels pa
        JOIN properties p ON pa.property_id = p.id
        JOIN clients c ON p.client_id = c.id
        WHERE c.report_id = ?
        """, (r["id"],))
        p_count = cursor.fetchone()[0]
        
        # Get total property count
        cursor.execute("""
        SELECT COUNT(DISTINCT p.id) FROM properties p
        JOIN clients c ON p.client_id = c.id
        WHERE c.report_id = ?
        """, (r["id"],))
        prop_count = cursor.fetchone()[0]
        
        evolution.append({
            "report_id": r["id"],
            "report_name": r["name"],
            "report_date": r["report_date"],
            "clients": c_count,
            "properties": prop_count,
            "parcels": p_count
        })
        
    transitions = []
    for i in range(len(reports) - 1):
        r_current = reports[i]
        r_next = reports[i+1]
        
        cursor.execute("SELECT name FROM clients WHERE report_id = ?", (r_current["id"],))
        clients_current = set(row[0] for row in cursor.fetchall())
        
        cursor.execute("SELECT name FROM clients WHERE report_id = ?", (r_next["id"],))
        clients_next = set(row[0] for row in cursor.fetchall())
        
        if not clients_current:
            continue
            
        # Contacted clients in action_logs based on chronological report_date range
        cursor.execute("""
        SELECT DISTINCT client_name FROM action_logs
        WHERE sent_at >= ? AND sent_at <= ?
        """, (r_current["report_date"] + " 00:00:00", r_next["report_date"] + " 23:59:59"))
        contacted_clients = set(row[0] for row in cursor.fetchall())
        
        contacted_default = clients_current.intersection(contacted_clients)
        uncontacted_default = clients_current.difference(contacted_default)
        
        recovered_all = clients_current.difference(clients_next)
        recovered_contacted = contacted_default.difference(clients_next)
        recovered_uncontacted = uncontacted_default.difference(clients_next)
        
        rate_contacted = len(recovered_contacted) / len(contacted_default) if contacted_default else 0.0
        rate_uncontacted = len(recovered_uncontacted) / len(uncontacted_default) if uncontacted_default else 0.0
        
        transitions.append({
            "from_report": r_current["name"],
            "to_report": r_next["name"],
            "contacted_total": len(contacted_default),
            "contacted_recovered": len(recovered_contacted),
            "contacted_rate": round(rate_contacted * 100, 1),
            "uncontacted_total": len(uncontacted_default),
            "uncontacted_recovered": len(recovered_uncontacted),
            "uncontacted_rate": round(rate_uncontacted * 100, 1),
            "total_recovery_rate": round(len(recovered_all) / len(clients_current) * 100, 1)
        })
        
    conn.close()
    return {
        "evolution": evolution,
        "transitions": transitions
    }

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/api/reports':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, report_name, report_date, imported_at FROM reports ORDER BY report_date DESC, id DESC")
            reports = [{"id": r[0], "report_name": r[1], "report_date": r[2], "imported_at": r[3]} for r in cursor.fetchall()]
            conn.close()
            
            self.wfile.write(json.dumps(reports, ensure_ascii=False).encode('utf-8'))
            
        elif self.path.startswith('/api/reports/'):
            parts = self.path.split('/')
            try:
                report_id = int(parts[-1])
                clients_data = get_clients_for_report(report_id)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(clients_data, ensure_ascii=False).encode('utf-8'))
            except ValueError:
                self.send_response(400)
                self.end_headers()
                
        elif self.path == '/api/clients':
            # Backward compatibility endpoint: returns the latest report clients
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            
            if row:
                clients_data = get_clients_for_report(row[0])
                self.wfile.write(json.dumps(clients_data, ensure_ascii=False).encode('utf-8'))
            else:
                self.wfile.write(b'{}')
                
        elif self.path == '/api/sent' or self.path == '/api/actions/sent':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT client_name FROM action_logs")
            sent_names = [r[0] for r in cursor.fetchall()]
            conn.close()
            
            self.wfile.write(json.dumps(sent_names, ensure_ascii=False).encode('utf-8'))
            
        elif self.path == '/api/kpis':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            kpi_data = get_kpis_data()
            self.wfile.write(json.dumps(kpi_data, ensure_ascii=False).encode('utf-8'))
            
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/reports' or self.path == '/api/clients':
            # Add new PDF report (contains report_name and clients object)
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                
                # Check format
                report_name = payload.get("report_name", f"Relatório Importado {time.strftime('%d/%m/%Y %H:%M')}")
                clients_data = payload.get("clients", {})
                
                if not clients_data and isinstance(payload, dict) and "report_name" not in payload:
                    # In case of backward compatibility call containing only the clients object
                    clients_data = payload
                
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                report_date = payload.get("report_date", time.strftime('%Y-%m-%d'))
                cursor.execute("INSERT INTO reports (report_name, report_date) VALUES (?, ?)", (report_name, report_date))
                report_id = cursor.lastrowid
                
                for c_name, c_data in clients_data.items():
                    cursor.execute("""
                    INSERT INTO clients (report_id, name, cpf_cnpj, cel, email)
                    VALUES (?, ?, ?, ?, ?)
                    """, (report_id, c_name, c_data.get('cpf_cnpj', ''), c_data.get('cel', ''), c_data.get('email', '')))
                    client_id = cursor.lastrowid
                    
                    for prop in c_data.get('properties', []):
                        cursor.execute("""
                        INSERT INTO properties (client_id, venda_id, identifier)
                        VALUES (?, ?, ?)
                        """, (client_id, prop.get('venda_id', ''), prop.get('identifier', '')))
                        property_id = cursor.lastrowid
                        
                        for parc in prop.get('parcels', []):
                            cursor.execute("""
                            INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full)
                            VALUES (?, ?, ?, ?)
                            """, (property_id, parc.get('parcela', ''), parc.get('vencimento', ''), parc.get('vencimento_full', '')))
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "report_id": report_id}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
                
        elif self.path == '/api/actions/sent' or self.path == '/api/sent':
            # Register a WhatsApp collection message log
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                
                # Compatibility: could be {"venda_id": "...", "client_name": "..."} or a list of client names
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                if isinstance(payload, list):
                    # Legacy batch sync
                    for name in payload:
                        cursor.execute("SELECT COUNT(*) FROM action_logs WHERE client_name = ?", (name,))
                        if cursor.fetchone()[0] == 0:
                            cursor.execute("INSERT INTO action_logs (venda_id, client_name) VALUES (?, ?)", ("0000", name))
                else:
                    venda_id = payload.get("venda_id", "0000")
                    client_name = payload.get("client_name", "")
                    if client_name:
                        cursor.execute("INSERT INTO action_logs (venda_id, client_name) VALUES (?, ?)", (venda_id, client_name))
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except Exception as e:
                self.send_response(400)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    try:
        init_db()
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

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    time.sleep(0.5)

    url = f"http://localhost:{PORT}/inad_whatsapp.html"
    print(f"Abrindo navegador em: {url}")
    
    try:
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
