"""
INAD — Painel de Cobrança
Servidor local multiplataforma (macOS, Windows, Linux/Servidor)

Uso:
  python3 run.py                    → Inicia na porta 8000 e abre o navegador
  INAD_PORT=9090 python3 run.py     → Usa a porta 9090
  INAD_HEADLESS=1 python3 run.py    → Modo servidor (sem abrir o navegador)
  python3 run.py --headless         → Igual ao modo servidor
  INAD_DEMO=1 python3 run.py        → Modo demo (banco isolado inad_demo.db)
  python3 run.py --demo             → Igual ao modo demo
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
import socket
import subprocess

# Windows: garante UTF-8 no console/redirecionamento (evita crash do banner
# com caracteres Unicode sob cp1252)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ─── CONFIGURAÇÃO DO SERVIDOR ──────────────────────────────────────────────────
# Define a porta padrão do servidor HTTP. O sistema tenta ler a variável de
# ambiente 'INAD_PORT' primeiro. Se não existir, assume a porta 8000.
PORT      = int(os.environ.get("INAD_PORT", 8000))

# Analisa os argumentos da linha de comando (CLI) para customizar a porta de execução.
# Suporta os formatos:
#   python run.py --port 9090
#   python run.py --port=9090
for _i, _arg in enumerate(sys.argv):
    if _arg == "--port" and _i + 1 < len(sys.argv):
        try:
            PORT = int(sys.argv[_i + 1])
        except ValueError:
            pass
    elif _arg.startswith("--port="):
        try:
            PORT = int(_arg.split("=", 1)[1])
        except ValueError:
            pass

DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Configuração de Logs de Erro Persistentes
# Inicializa o logger padrão do Python para capturar erros críticos de execução do servidor.
# Os registros de falha são salvos no arquivo 'inad_errors.log' na pasta raiz do projeto.
# Isso garante o registro detalhado de exceções em qualquer sistema (Windows, macOS ou Linux).
import logging
logging.basicConfig(
    filename=os.path.join(DIRECTORY, "inad_errors.log"),
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Modo demo: banco totalmente isolado (inad_demo.db) para testes com dados
# fictícios, sem nunca ler ou gravar o inad_database.db real.
DEMO      = os.environ.get("INAD_DEMO", "0").strip() == "1" or "--demo" in sys.argv
DB_FILE   = "inad_demo.db" if DEMO else "inad_database.db"
DB_PATH   = os.path.join(DIRECTORY, DB_FILE)

# Porta onde uma instância demo (lançada pelo botão "🧪 Modo Demo" da UI)
# deve escutar. Só usada pelo servidor real para orquestrar o child process.
DEMO_PORT = int(os.environ.get("INAD_DEMO_PORT", PORT + 1000))

# Modo headless: ativado via arg --headless, var INAD_HEADLESS=1,
# ou quando o sistema não tiver display (servidores Linux sem GUI).
_headless_env  = os.environ.get("INAD_HEADLESS", "0").strip() == "1"
_headless_arg  = "--headless" in sys.argv
_no_display    = (platform.system() == "Linux"
                  and not os.environ.get("DISPLAY")
                  and not os.environ.get("WAYLAND_DISPLAY"))
HEADLESS = _headless_env or _headless_arg or _no_display

# ─── REGRAS DE NEGÓCIO E CONSTANTES OPERACIONAIS (v3.0.0) ────────────────────
AGING_BUCKETS = [(0, 30, '0-30'), (31, 60, '31-60'), (61, 90, '61-90'), (91, 120, '91-120'), (121, None, '120+')]
PREJURIDICO_DAYS = 120
STAGES = {'0-30': 'lembrete', '31-60': 'firme', '61-90': 'firme', '91-120': 'serio', '120+': 'pre_juridico'}
RISK_WEIGHT_VALOR = 45.0
RISK_WEIGHT_AGING = 35.0
RISK_WEIGHT_REINCIDENCIA = 20.0
OUTCOME_TYPES = ("prometeu_pagar", "negociacao", "pagou", "sem_resposta", "numero_invalido", "recusou", "outro")

# Nota de compliance do art. 42 do CDC / Lei 9.514/97:
# Conforme o CDC art. 42, a cobrança não pode expor o cliente a ridículo, constrangimento ou ameaça.
# Todos os templates — inclusive o de pré-jurídico — devem ser factuais, respeitosos e limitados aos dados
# do débito (parcelas, valores, vencimentos) e a canais de regularização. O template pré-jurídico deve
# informar que o caso "poderá ser encaminhado ao setor jurídico" — nunca ameaçar processo, negativação
# ou perda do imóvel. No financiamento com alienação fiduciária (Lei 9.514/97), os passos formais (notificação
# via cartório, purga da mora) são atos jurídicos conduzidos por humanos/advogados; a ferramenta não automatiza
# nenhum passo legal — o estágio pre_juridico é apenas uma fila interna para triagem humana e entrega ao jurídico.
# Isto não é aconselhamento jurídico.

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
            valor           REAL    DEFAULT 0.0,
            FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS action_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            venda_id    TEXT    NOT NULL,
            client_name TEXT    NOT NULL,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS kpi_exclusions (
            client_name TEXT    PRIMARY KEY
        );

        -- 7. Desfechos de contato (outcomes)
        CREATE TABLE IF NOT EXISTS contact_outcomes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name   TEXT    NOT NULL,
            venda_id      TEXT    DEFAULT '',
            action_log_id INTEGER,
            outcome       TEXT    NOT NULL,
            promised_date TEXT,
            next_contact  TEXT,
            note          TEXT    DEFAULT '',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_clients_name         ON clients(name);
        CREATE INDEX IF NOT EXISTS idx_clients_report_id    ON clients(report_id);
        CREATE INDEX IF NOT EXISTS idx_properties_client_id ON properties(client_id);
        CREATE INDEX IF NOT EXISTS idx_parcels_property_id  ON parcels(property_id);
        CREATE INDEX IF NOT EXISTS idx_parcels_venc         ON parcels(vencimento_full);
        CREATE INDEX IF NOT EXISTS idx_outcomes_client      ON contact_outcomes(client_name);
        CREATE INDEX IF NOT EXISTS idx_outcomes_created     ON contact_outcomes(created_at);
    """)

    # Migração: adiciona coluna report_date se não existir (banco legado)
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(reports)")}
    if "report_date" not in existing_cols:
        cursor.execute("ALTER TABLE reports ADD COLUMN report_date TEXT")
        print("[MIGRAÇÃO] Coluna report_date adicionada à tabela reports.")

    # Migração: adiciona coluna valor se não existir (banco legado)
    existing_parcel_cols = {row[1] for row in cursor.execute("PRAGMA table_info(parcels)")}
    if "valor" not in existing_parcel_cols:
        cursor.execute("ALTER TABLE parcels ADD COLUMN valor REAL DEFAULT 0.0")
        print("[MIGRAÇÃO] Coluna valor adicionada à tabela parcels.")

    conn.commit()

    # Modo demo: nenhum dado real (JSONs legados, backfill, seed de exclusões)
    # pode entrar no banco demo — apenas o schema é criado. Na primeira vez
    # (banco vazio), popula automaticamente com dados fictícios para que o
    # botão "🧪 Modo Demo" da UI funcione sem passos manuais no terminal.
    if DEMO:
        if cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0:
            try:
                import generate_demo_data
                generate_demo_data.generate()
                print("[DEMO] Banco demo populado automaticamente com dados fictícios.")
            except Exception as exc:
                print(f"[DEMO] Falha ao gerar dados fictícios automaticamente: {exc}")
        return

    _migrate_legacy_files(cursor, conn)

    # Backfill de valores de parcelas a partir de clients_data.json se as parcelas no banco estiverem zeradas
    clients_path = os.path.join(DIRECTORY, "clients_data.json")
    try:
        cursor.execute("SELECT COUNT(*) FROM parcels WHERE valor > 0.0")
        if cursor.fetchone()[0] == 0 and os.path.exists(clients_path):
            print("[MIGRAÇÃO] Iniciando backfill de valores de parcelas...")
            with open(clients_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            
            values_map = {}
            for c_name, c_data in legacy.items():
                for prop in c_data.get("properties", []):
                    p_ident = prop.get("identifier", "")
                    for parc in prop.get("parcels", []):
                        p_num = parc.get("parcela", "")
                        val = float(parc.get("valor") or parc.get("valor_total") or parc.get("valor_parcela") or 0.0)
                        if val > 0.0:
                            values_map[(c_name, p_ident, p_num)] = val
            
            db_parcels = cursor.execute("""
                SELECT pa.id, c.name, p.identifier, pa.parcela
                FROM parcels pa
                JOIN properties p ON pa.property_id = p.id
                JOIN clients c ON p.client_id = c.id
                WHERE pa.valor = 0.0
            """).fetchall()
            
            updates = []
            for pa_id, c_name, p_ident, pa_num in db_parcels:
                val = values_map.get((c_name, p_ident, pa_num))
                if val:
                    updates.append((val, pa_id))
            
            if updates:
                cursor.executemany("UPDATE parcels SET valor = ? WHERE id = ?", updates)
                conn.commit()
                print(f"[MIGRAÇÃO] {len(updates)} parcelas atualizadas com o valor real.")
    except Exception as exc:
        print(f"[MIGRAÇÃO] Erro no backfill de valores de parcelas: {exc}")

    _apply_kpi_exclusions_seed(cursor, conn)


def _apply_kpi_exclusions_seed(cursor, conn):
    """Carrega exclusões padrão de kpi_exclusions.json (versionado no Git)."""
    seed_path = os.path.join(DIRECTORY, "kpi_exclusions.json")
    if not os.path.exists(seed_path):
        return
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            names = json.load(f)
        if not isinstance(names, list):
            return
        added = 0
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            cursor.execute(
                "INSERT OR IGNORE INTO kpi_exclusions (client_name) VALUES (?)",
                (name.strip(),),
            )
            if cursor.rowcount:
                added += 1
        if added:
            conn.commit()
            print(f"[KPI] {added} exclusão(ões) aplicada(s) de kpi_exclusions.json.")
    except Exception as exc:
        print(f"[KPI] Erro ao carregar kpi_exclusions.json: {exc}")


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
                    "INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full, valor) "
                    "VALUES (?,?,?,?,?)",
                    (property_id, parc.get("parcela", ""),
                     parc.get("vencimento", ""), parc.get("vencimento_full", ""),
                     float(parc.get("valor") or parc.get("valor_total") or parc.get("valor_parcela") or 0.0)),
                )


# ─── QUERIES DE DADOS ─────────────────────────────────────────────────────────

def get_clients_for_report(report_id):
    """Retorna a árvore de clientes/imóveis/parcelas de um relatório como dict."""
    cursor = get_conn().cursor()
    rows = cursor.execute("""
        SELECT c.name, c.cpf_cnpj, c.cel, c.email,
               p.venda_id, p.identifier,
               pa.parcela, pa.vencimento, pa.vencimento_full,
               COALESCE(pa.valor, 0.0)
        FROM   clients c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  c.report_id = ?
        ORDER  BY c.name, p.venda_id, pa.parcela
    """, (report_id,)).fetchall()

    result = {}
    for row in rows:
        c_name, c_cpf, c_cel, c_email, p_vid, p_ident, pa_num, pa_venc, pa_venc_f, pa_val = row
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
                "parcela": pa_num, "vencimento": pa_venc, "vencimento_full": pa_venc_f, "valor": pa_val
            })
    return result


# ─── FUNÇÕES AUXILIARES OPERACIONAIS E DE RISCO (v3.0.0) ──────────────────────

def _dedup_latest_report_id(cursor):
    """Retorna o ID do relatório mais recente deduplicado por data."""
    rows = cursor.execute("""
        SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
        FROM   reports
        ORDER  BY rdate DESC, id DESC
    """).fetchall()
    if not rows:
        return None
    seen_dates = set()
    latest_ids = []
    for rid, rdate in rows:
        if rdate not in seen_dates:
            seen_dates.add(rdate)
            latest_ids.append(rid)
    return latest_ids[0] if latest_ids else None


def _client_financials(cursor, report_id, ref_date):
    """
    Agrupa dados financeiros dos inadimplentes de um relatório até a data ref_date.
    Ignora parcelas futuras (vencimento_full > ref_date).
    """
    rows = cursor.execute("""
        SELECT c.name, c.cel, c.email,
               COUNT(DISTINCT p.id)                          AS n_properties,
               COUNT(pa.id)                                  AS n_parcels,
               COALESCE(SUM(pa.valor), 0.0)                  AS total_owed,
               COALESCE(AVG(pa.valor), 0.0)                  AS avg_parcel,
               MIN(pa.vencimento_full)                       AS oldest_due,
               CAST(julianday(?) - julianday(MIN(pa.vencimento_full)) AS INTEGER) AS max_days_overdue
        FROM clients c
        LEFT JOIN properties p ON p.client_id = c.id
        LEFT JOIN parcels pa   ON pa.property_id = p.id AND pa.vencimento_full <= ?
        WHERE c.report_id = ?
          AND c.name NOT IN (SELECT client_name FROM kpi_exclusions)
        GROUP BY c.name
    """, (ref_date, ref_date, report_id)).fetchall()

    result = {}
    for r in rows:
        name = r[0]
        oldest_due = r[7]
        max_days = r[8] if oldest_due is not None else 0
        if max_days < 0:
            max_days = 0
        result[name] = {
            "name": name,
            "cel": r[1] or "",
            "email": r[2] or "",
            "n_properties": r[3],
            "n_parcels": r[4],
            "total_owed": round(r[5], 2),
            "avg_parcel": round(r[6], 2),
            "oldest_due": oldest_due,
            "max_days_overdue": max_days
        }
    return result


def _bucketize(days):
    """Mapeia dias de atraso para o bucket correspondente."""
    for start, end, label in AGING_BUCKETS:
        if end is None:
            if days >= start:
                return label
        elif start <= days <= end:
            return label
    return '0-30'


def _stage_for_days(days):
    """Mapeia dias de atraso para o estágio de cobrança."""
    if days <= 30:
        return 'lembrete'
    elif days <= 90:
        return 'firme'
    elif days <= 120:
        return 'serio'
    else:
        return 'pre_juridico'


def _calculate_reentries(cursor):
    """
    Mapeia o número de reentradas e timeline cronológica de todos os clientes.
    """
    report_rows = cursor.execute("""
        SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
        FROM   reports
        ORDER  BY rdate ASC, id ASC
    """).fetchall()

    rdate_to_latest_id = {}
    for rid, rdate in report_rows:
        rdate_to_latest_id[rdate] = rid
    deduped_reports = sorted(
        [{"id": rid, "date": rdate} for rdate, rid in rdate_to_latest_id.items()],
        key=lambda x: x["date"]
    )

    if not deduped_reports:
        return {}

    presence_rows = cursor.execute("""
        SELECT report_id, name FROM clients
        WHERE name NOT IN (SELECT client_name FROM kpi_exclusions)
    """).fetchall()

    client_presence = {}
    for rid, name in presence_rows:
        if name not in client_presence:
            client_presence[name] = set()
        client_presence[name].add(rid)

    results = {}
    for name, rids in client_presence.items():
        timeline = []
        first_seen = None
        present_seq = []
        for r in deduped_reports:
            present = r["id"] in rids
            timeline.append({"report_date": r["date"], "present": present})
            if present:
                if first_seen is None:
                    first_seen = r["date"]
                present_seq.append(True)
            else:
                if first_seen is not None:
                    present_seq.append(False)

        reentry_count = 0
        if present_seq:
            last_state = True
            for state in present_seq[1:]:
                if state and not last_state:
                    reentry_count += 1
                last_state = state

        results[name] = {
            "reentries": reentry_count,
            "timeline": timeline,
            "first_seen": first_seen,
            "currently_present": (deduped_reports[-1]["id"] in rids) if deduped_reports else False
        }
    return results


def _calculate_risk_score(total_owed, max_days_overdue, reentry_count, p90_total_owed):
    """Retorna score final e componentes."""
    p90 = p90_total_owed if p90_total_owed > 0 else 1.0
    v = min(total_owed / p90, 1.0)
    a = min(max_days_overdue / 180.0, 1.0)
    r = min(reentry_count / 3.0, 1.0)
    score = round(RISK_WEIGHT_VALOR * v + RISK_WEIGHT_AGING * a + RISK_WEIGHT_REINCIDENCIA * r, 1)
    return {
        "score": score,
        "components": {
            "valor": round(v, 2),
            "aging": round(a, 2),
            "reincidencia": round(r, 2)
        }
    }


def _contact_effectiveness(cursor):
    """
    Calcula eficácia dos envios e promessas cumpridas.
    """
    report_rows = cursor.execute("""
        SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
        FROM   reports
        ORDER  BY rdate ASC, id ASC
    """).fetchall()

    rdate_to_latest_id = {}
    for rid, rdate in report_rows:
        rdate_to_latest_id[rdate] = rid
    deduped_reports = sorted(
        [{"id": rid, "date": rdate} for rdate, rid in rdate_to_latest_id.items()],
        key=lambda x: x["date"]
    )

    report_clients = {}
    for r in deduped_reports:
        rows = cursor.execute("SELECT name FROM clients WHERE report_id = ?", (r["id"],)).fetchall()
        report_clients[r["id"]] = {row[0] for row in rows}

    contacts = cursor.execute("""
        SELECT client_name, MAX(sent_at) FROM action_logs
        GROUP BY client_name
    """).fetchall()

    contacted = 0
    regularized = 0
    for name, sent_at in contacts:
        sent_date = sent_at.split()[0] if sent_at else ""
        subsequent_report_id = None
        for r in deduped_reports:
            if r["date"] > sent_date:
                subsequent_report_id = r["id"]
                break
        if subsequent_report_id is not None:
            contacted += 1
            if name not in report_clients[subsequent_report_id]:
                regularized += 1

    promises = cursor.execute("""
        SELECT client_name, promised_date FROM contact_outcomes
        WHERE outcome = 'prometeu_pagar' AND promised_date IS NOT NULL
    """).fetchall()

    promises_made = len(promises)
    promises_kept = 0
    for name, promised_date in promises:
        subsequent_report_id = None
        for r in deduped_reports:
            if r["date"] > promised_date:
                subsequent_report_id = r["id"]
                break
        if subsequent_report_id is not None:
            if name not in report_clients[subsequent_report_id]:
                promises_kept += 1

    rate = round(regularized / contacted * 100, 1) if contacted > 0 else 0.0
    return {
        "contacted": contacted,
        "regularized_after_contact": regularized,
        "rate": rate,
        "promises_made": promises_made,
        "promises_kept": promises_kept
    }


def _get_worklist_data(cursor, ref_date):
    """
    Retorna as categorias de worklist (alertas operacionais).
    """
    import datetime
    report_id = _dedup_latest_report_id(cursor)
    if not report_id:
        return {
            "promessas_vencidas": [],
            "recontato_agendado": [],
            "sem_resposta": [],
            "novos_pre_juridico": []
        }

    cf_all = _client_financials(cursor, report_id, ref_date)
    if not cf_all:
        return {
            "promessas_vencidas": [],
            "recontato_agendado": [],
            "sem_resposta": [],
            "novos_pre_juridico": []
        }

    # P90, Reentries, Contacts, Venda ids, Outcomes
    vals = sorted([x["total_owed"] for x in cf_all.values()])
    idx = int(len(vals) * 0.9) if vals else 0
    p90 = vals[idx] if vals else 0.0

    reentries_map = _calculate_reentries(cursor)

    outcomes_rows = cursor.execute("""
        SELECT client_name, outcome, promised_date, next_contact, note, created_at
        FROM contact_outcomes
        WHERE (client_name, created_at) IN (
            SELECT client_name, MAX(created_at) FROM contact_outcomes GROUP BY client_name
        )
    """).fetchall()
    latest_outcomes = {
        r[0]: {
            "outcome": r[1], "promised_date": r[2], "next_contact": r[3], "note": r[4], "created_at": r[5]
        } for r in outcomes_rows
    }

    contact_rows = cursor.execute("""
        SELECT client_name, MAX(sent_at) FROM action_logs GROUP BY client_name
    """).fetchall()
    latest_contacts = {r[0]: r[1] for r in contact_rows}

    venda_rows = cursor.execute("""
        SELECT c.name, p.venda_id FROM properties p
        JOIN clients c ON p.client_id = c.id
        WHERE c.report_id = ?
    """, (report_id,)).fetchall()
    venda_map = {}
    for cname, vid in venda_rows:
        if cname not in venda_map:
            venda_map[cname] = []
        venda_map[cname].append(vid)

    # Novos Pré-Jurídico detection
    prev_report_id = None
    report_rows = cursor.execute("""
        SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
        FROM   reports
        ORDER  BY rdate DESC, id DESC
    """).fetchall()

    seen_dates = set()
    latest_ids = []
    for rid, rdate in report_rows:
        if rdate not in seen_dates:
            seen_dates.add(rdate)
            latest_ids.append((rid, rdate))

    prev_report_date = None
    if len(latest_ids) > 1:
        prev_report_id = latest_ids[1][0]
        prev_report_date = latest_ids[1][1]

    prev_cf = {}
    if prev_report_id:
        prev_cf = _client_financials(cursor, prev_report_id, prev_report_date)

    promessas_vencidas = []
    recontato_agendado = []
    sem_resposta = []
    novos_pre_juridico = []

    categorized_clients = set()

    for name, cf in cf_all.items():
        reentries = reentries_map.get(name, {}).get("reentries", 0)
        score_info = _calculate_risk_score(cf["total_owed"], cf["max_days_overdue"], reentries, p90)
        bucket = _bucketize(cf["max_days_overdue"])
        stage = _stage_for_days(cf["max_days_overdue"])

        last_c = latest_contacts.get(name)
        out_info = latest_outcomes.get(name)
        out_outcome = out_info["outcome"] if out_info else None
        out_date = out_info["created_at"] if out_info else None
        
        last_outcome = out_outcome
        last_outcome_date = out_date
        if last_c and (not out_date or last_c > out_date):
            last_outcome = "sem_resposta"
            last_outcome_date = last_c

        queue_row = {
            "name": name,
            "cel": cf["cel"],
            "email": cf["email"],
            "venda_ids": venda_map.get(name, []),
            "total_owed": cf["total_owed"],
            "avg_parcel": cf["avg_parcel"],
            "n_properties": cf["n_properties"],
            "n_parcels": cf["n_parcels"],
            "max_days_overdue": cf["max_days_overdue"],
            "bucket": bucket,
            "stage": stage,
            "reentries": reentries,
            "risk_score": score_info["score"],
            "components": score_info["components"],
            "last_contact": last_c,
            "last_outcome": last_outcome,
            "promised_date": out_info["promised_date"] if out_info else None,
            "next_contact": out_info["next_contact"] if out_info else None,
            "last_outcome_date": last_outcome_date
        }

        # 1) Promessas vencidas
        out_info = latest_outcomes.get(name)
        if out_info and out_info["outcome"] == "prometeu_pagar" and out_info["promised_date"]:
            if out_info["promised_date"] < ref_date:
                try:
                    p_dt = datetime.datetime.strptime(out_info["promised_date"], "%Y-%m-%d").date()
                    ref_dt = datetime.datetime.strptime(ref_date, "%Y-%m-%d").date()
                    days_late = (ref_dt - p_dt).days
                except Exception:
                    days_late = 0
                item = dict(queue_row)
                item["days_late_on_promise"] = days_late
                promessas_vencidas.append(item)
                categorized_clients.add(name)
                continue

        # 2) Recontato agendado
        if out_info and out_info["next_contact"] and out_info["next_contact"] <= ref_date:
            item = dict(queue_row)
            recontato_agendado.append(item)
            categorized_clients.add(name)
            continue

        # 3) Sem resposta
        last_c = latest_contacts.get(name)
        if last_c:
            last_c_date = last_c.split()[0]
            try:
                c_dt = datetime.datetime.strptime(last_c_date, "%Y-%m-%d").date()
                ref_dt = datetime.datetime.strptime(ref_date, "%Y-%m-%d").date()
                days_since = (ref_dt - c_dt).days
            except Exception:
                days_since = 0

            if days_since >= 7:
                has_valid_outcome = False
                if out_info:
                    outcome_date = out_info["created_at"].split()[0]
                    if outcome_date >= last_c_date:
                        if out_info["outcome"] != "sem_resposta":
                            has_valid_outcome = True

                if not has_valid_outcome:
                    item = dict(queue_row)
                    item["days_since_contact"] = days_since
                    sem_resposta.append(item)
                    categorized_clients.add(name)
                    continue

        # 4) Novos Pré-Jurídico
        if cf["max_days_overdue"] > 120:
            if prev_report_id:
                prev_cf_client = prev_cf.get(name)
                if prev_cf_client and prev_cf_client["max_days_overdue"] <= 120:
                    item = dict(queue_row)
                    item["entered_bucket"] = True
                    novos_pre_juridico.append(item)
                    categorized_clients.add(name)
                    continue

    return {
        "promessas_vencidas": promessas_vencidas,
        "recontato_agendado": recontato_agendado,
        "sem_resposta": sem_resposta,
        "novos_pre_juridico": novos_pre_juridico
    }


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

    # Uma única query GROUP BY para stats de todos os relatórios (filtrando excluídos)
    all_stats_rows = cursor.execute("""
        SELECT c.report_id,
               COUNT(DISTINCT c.id)   AS clients,
               COUNT(DISTINCT p.id)   AS properties,
               COUNT(pa.id)           AS parcels,
               COALESCE(SUM(pa.valor), 0.0) AS total_value
        FROM   clients   c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  c.name NOT IN (SELECT client_name FROM kpi_exclusions)
        GROUP  BY c.report_id
    """).fetchall()
    all_stats_map = {r[0]: {"clients": r[1], "properties": r[2], "parcels": r[3], "total_value": round(r[4], 2)}
                     for r in all_stats_rows}

    # Identifica o ID do relatório mais recente para cada data real (Deduplicação Global)
    rdate_to_latest_id = {}
    for r in all_reports:
        # Sobrescreve mantendo sempre o maior ID (mais recente) para aquela data real
        rdate_to_latest_id[r["report_date"]] = r["id"]

    active_ids_global = set(rdate_to_latest_id.values())

    all_evolution = [
        {
            "report_id":   r["id"],
            "report_name": r["name"],
            "report_date": r["report_date"],
            "is_duplicate": r["id"] not in active_ids_global,
            **all_stats_map.get(r["id"], {"clients": 0, "properties": 0, "parcels": 0, "total_value": 0.0}),
        }
        for r in all_reports
    ]

    # Aplica o filtro de IDs selecionados, se fornecido
    if report_ids is not None:
        selected_reports = [r for r in all_reports if r["id"] in report_ids]
    else:
        # Por padrão, apenas relatórios não-duplicados são considerados nos KPIs ativos
        selected_reports = [r for r in all_reports if r["id"] in active_ids_global]

    # Deduplica os relatórios selecionados na data real (segurança adicional)
    selected_deduped = {}
    for r in selected_reports:
        selected_deduped[r["report_date"]] = r
    
    reports = sorted(selected_deduped.values(), key=lambda x: (x["report_date"], x["id"]))
    active_report_ids = [r["id"] for r in reports]
    evolution = [e for e in all_evolution if e["report_id"] in active_report_ids]

    # Busca todos os clientes dos relatórios filtrados (filtrando excluídos)
    if report_ids is not None:
        if report_ids:
            placeholders = ",".join("?" for _ in report_ids)
            client_rows = cursor.execute(
                f"SELECT report_id, name FROM clients WHERE report_id IN ({placeholders}) "
                f"AND name NOT IN (SELECT client_name FROM kpi_exclusions)",
                report_ids
            ).fetchall()
        else:
            client_rows = []
    else:
        client_rows = cursor.execute("""
            SELECT report_id, name FROM clients 
            WHERE name NOT IN (SELECT client_name FROM kpi_exclusions)
        """).fetchall()

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

        recovered = clients_cur - clients_next
        recovery_rate = round(len(recovered) / len(clients_cur) * 100, 1)

        transitions.append({
            "from_report":       r_cur["name"],
            "to_report":         r_next["name"],
            "total_clients":     len(clients_cur),
            "recovered_clients": len(recovered),
            "recovery_rate":     recovery_rate,
        })

    return {
        "evolution": evolution,
        "transitions": transitions,
        "all_evolution": all_evolution
    }


# CTE compartilhada: primeira aparição de cada cliente em TODO o histórico.
# Nunca deve ser restrita por filtros de data — o segmento novo/antigo depende
# da estreia global do cliente, não da janela exibida.
_FIRST_SEEN_CTE = """
    WITH report_dates AS (
        SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
        FROM   reports
    ),
    first_seen AS (
        SELECT c.name AS name, MIN(rd.rdate) AS first_date
        FROM   clients c
        JOIN   report_dates rd ON rd.id = c.report_id
        GROUP  BY c.name
    )
"""


def get_analytics_data(start=None, end=None, report_ids=None,
                       segment="all", cutoff=None, cutoff_last_n=None):
    """
    Dados agregados para a página de Analytics: série temporal por segmento
    (novo/antigo/total), transições com taxa de recuperação por segmento e
    totais do período. Identidade de cliente é por nome exato (limitação
    conhecida: variações de grafia/acento contam como clientes distintos).
    """
    cursor = get_conn().cursor()

    all_report_rows = cursor.execute("""
        SELECT id, report_name,
               COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate,
               imported_at
        FROM   reports
        ORDER  BY rdate ASC, id ASC
    """).fetchall()
    all_reports = [{"id": r[0], "name": r[1], "report_date": r[2], "imported_at": r[3]}
                   for r in all_report_rows]

    # Versão dos dados: muda sempre que um relatório é importado/excluído.
    # O frontend faz polling barato disso para saber se há dados novos.
    ver_row = cursor.execute(
        "SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(MAX(id), 0) FROM reports"
    ).fetchone()
    data_version = f"{ver_row[0]}:{ver_row[2]}:{ver_row[1]}"

    if not all_reports:
        return {
            "meta": {
                "cutoff_date": None, "cutoff_mode": None,
                "segment_filter": segment,
                "date_range": {"start": start, "end": end},
                "available_date_range": {"min": None, "max": None},
                "data_version": data_version,
            },
            "series": [], "transitions": [],
            "segment_totals": {"novo": {}, "antigo": {}},
        }

    # Deduplicação global por data real (mantém o maior ID de cada data)
    rdate_to_latest_id = {}
    for r in all_reports:
        rdate_to_latest_id[r["report_date"]] = r["id"]
    active_ids = set(rdate_to_latest_id.values())

    distinct_dates = sorted(rdate_to_latest_id.keys())
    available_range = {"min": distinct_dates[0], "max": distinct_dates[-1]}

    # Resolve a data de corte novo/antigo
    if cutoff:
        cutoff_date, cutoff_mode = cutoff, "date"
    else:
        n = cutoff_last_n if cutoff_last_n and cutoff_last_n > 0 else 1
        n = min(n, len(distinct_dates))
        cutoff_date, cutoff_mode = distinct_dates[-n], "last_n"

    # Seleção de relatórios: dedup + intervalo de datas + IDs explícitos
    selected = [r for r in all_reports if r["id"] in active_ids]
    if start:
        selected = [r for r in selected if r["report_date"] >= start]
    if end:
        selected = [r for r in selected if r["report_date"] <= end]
    if report_ids is not None:
        selected = [r for r in selected if r["id"] in report_ids]
    selected.sort(key=lambda r: (r["report_date"], r["id"]))

    # Agregados por relatório × segmento (uma query só)
    seg_rows = cursor.execute(_FIRST_SEEN_CTE + """
        SELECT c.report_id,
               CASE WHEN fs.first_date >= ? THEN 'novo' ELSE 'antigo' END AS segment,
               COUNT(DISTINCT c.id)         AS clients,
               COUNT(DISTINCT p.id)         AS properties,
               COUNT(pa.id)                 AS parcels,
               COALESCE(SUM(pa.valor), 0.0) AS total_value
        FROM   clients c
        JOIN   first_seen fs    ON fs.name = c.name
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  c.name NOT IN (SELECT client_name FROM kpi_exclusions)
        GROUP  BY c.report_id, segment
    """, (cutoff_date,)).fetchall()

    _empty = {"clients": 0, "properties": 0, "parcels": 0, "total_value": 0.0}
    seg_map = {}   # report_id -> {"novo": {...}, "antigo": {...}}
    for rid, seg, n_cli, n_prop, n_parc, val in seg_rows:
        seg_map.setdefault(rid, {})[seg] = {
            "clients": n_cli, "properties": n_prop,
            "parcels": n_parc, "total_value": round(val, 2),
        }

    series = []
    for r in selected:
        novo   = seg_map.get(r["id"], {}).get("novo",   dict(_empty))
        antigo = seg_map.get(r["id"], {}).get("antigo", dict(_empty))
        total  = {k: round(novo[k] + antigo[k], 2) for k in _empty}
        total["clients"]    = novo["clients"] + antigo["clients"]
        total["properties"] = novo["properties"] + antigo["properties"]
        total["parcels"]    = novo["parcels"] + antigo["parcels"]
        series.append({
            "report_id":   r["id"],
            "report_name": r["name"],
            "report_date": r["report_date"],
            "is_duplicate": False,
            "total": total, "novo": novo, "antigo": antigo,
        })

    # Clientes por relatório com segmento e valor (para transições/recuperação)
    sel_ids = [r["id"] for r in selected]
    client_rows = []
    if sel_ids:
        placeholders = ",".join("?" for _ in sel_ids)
        client_rows = cursor.execute(_FIRST_SEEN_CTE + f"""
            SELECT c.report_id, c.name,
                   CASE WHEN fs.first_date >= ? THEN 'novo' ELSE 'antigo' END AS segment,
                   COALESCE(SUM(pa.valor), 0.0) AS value
            FROM   clients c
            JOIN   first_seen fs    ON fs.name = c.name
            LEFT JOIN properties p  ON p.client_id   = c.id
            LEFT JOIN parcels    pa ON pa.property_id = p.id
            WHERE  c.report_id IN ({placeholders})
              AND  c.name NOT IN (SELECT client_name FROM kpi_exclusions)
            GROUP  BY c.report_id, c.name
        """, [cutoff_date] + sel_ids).fetchall()

    per_report = {}   # report_id -> {name: (segment, value)}
    for rid, name, seg, val in client_rows:
        per_report.setdefault(rid, {})[name] = (seg, val)

    def _rate(recovered, total):
        return round(len(recovered) / len(total) * 100, 1) if total else 0.0

    transitions = []
    for i in range(len(selected) - 1):
        r_cur, r_next = selected[i], selected[i + 1]
        cur  = per_report.get(r_cur["id"],  {})
        nxt  = per_report.get(r_next["id"], {})
        if not cur:
            continue

        cur_names   = set(cur)
        recovered   = cur_names - set(nxt)
        cur_novo    = {n for n in cur_names if cur[n][0] == "novo"}
        cur_antigo  = cur_names - cur_novo

        transitions.append({
            "from_report":        r_cur["name"],
            "to_report":          r_next["name"],
            "from_date":          r_cur["report_date"],
            "to_date":            r_next["report_date"],
            "total_clients":      len(cur_names),
            "recovered_clients":  len(recovered),
            "recovery_rate":      _rate(recovered, cur_names),
            "recovery_rate_novo":   _rate(recovered & cur_novo,   cur_novo),
            "recovery_rate_antigo": _rate(recovered & cur_antigo, cur_antigo),
            "recovered_value":    round(sum(cur[n][1] for n in recovered), 2),
        })

    # Totais do segmento no relatório mais recente do período
    if series:
        last = series[-1]
        segment_totals = {
            "novo":   {"clients": last["novo"]["clients"],
                       "total_value": last["novo"]["total_value"]},
            "antigo": {"clients": last["antigo"]["clients"],
                       "total_value": last["antigo"]["total_value"]},
        }
    else:
        segment_totals = {"novo": {}, "antigo": {}}

    return {
        "meta": {
            "cutoff_date": cutoff_date,
            "cutoff_mode": cutoff_mode,
            "cutoff_last_n": cutoff_last_n if cutoff_mode == "last_n" else None,
            "segment_filter": segment,
            "date_range": {"start": start, "end": end},
            "available_date_range": available_range,
            "data_version": data_version,
        },
        "series": series,
        "transitions": transitions,
        "segment_totals": segment_totals,
    }


# ─── CONTEXTO PARA I.A. ───────────────────────────────────────────────────────

def get_system_context():
    """
    Retorna contexto estruturado do projeto para IAs, extensões e integrações.
    Consumido via GET /api/context — complementa o AI_CONTEXT.md em markdown.
    """
    cursor = get_conn().cursor()
    report_count = cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    client_count = cursor.execute("SELECT COUNT(DISTINCT name) FROM clients").fetchone()[0]
    sent_count   = cursor.execute(
        "SELECT COUNT(DISTINCT client_name) FROM action_logs"
    ).fetchone()[0]
    excluded_count = cursor.execute("SELECT COUNT(*) FROM kpi_exclusions").fetchone()[0]
    outcomes_count = cursor.execute("SELECT COUNT(*) FROM contact_outcomes").fetchone()[0]
    
    # Contagem de clientes pré-jurídicos no relatório mais recente
    pre_juridico_count = 0
    import datetime
    ref_date = datetime.date.today().isoformat()
    latest_rid = _dedup_latest_report_id(cursor)
    if latest_rid:
        pre_juridico_count = len(cursor.execute("""
            SELECT c.name FROM clients c
            LEFT JOIN properties p ON p.client_id = c.id
            LEFT JOIN parcels pa ON pa.property_id = p.id AND pa.vencimento_full <= ?
            WHERE c.report_id = ?
              AND c.name NOT IN (SELECT client_name FROM kpi_exclusions)
            GROUP BY c.name
            HAVING CAST(julianday(?) - julianday(MIN(pa.vencimento_full)) AS INTEGER) > 120
        """, (ref_date, latest_rid, ref_date)).fetchall())

    ai_context_path = os.path.join(DIRECTORY, "AI_CONTEXT.md")
    ai_context_md = ""
    if os.path.exists(ai_context_path):
        try:
            with open(ai_context_path, "r", encoding="utf-8") as f:
                ai_context_md = f.read()
        except OSError:
            pass

    return {
        "project": {
            "name": "INAD — Painel de Cobrança",
            "purpose": (
                "Painel local para importar PDFs de inadimplência, "
                "gerar mensagens de cobrança via WhatsApp e acompanhar KPIs de recuperação."
            ),
            "documentation_file": "AI_CONTEXT.md",
            "entry_point": "run.py",
            "frontend_template": "inad_template.html",
            "frontend_compiled": "inad_whatsapp.html",
            "compiler": "add_pdf_importer.py",
            "database_file": DB_FILE,
            "demo_mode": DEMO,
        },
        "architecture": {
            "pattern": "Servidor HTTP Python + SPA HTML/JS + SQLite local",
            "data_flow": [
                "PDF importado no navegador → parsing client-side (pdf.js + regex)",
                "Dados extraídos → POST /api/reports → SQLite",
                "WhatsApp aberto → POST /api/actions/sent → action_logs",
                "Desfecho de contato registrado → POST /api/outcomes → contact_outcomes",
                "Fallback file:// → localStorage (sem servidor)",
            ],
            "compile_step": (
                "Após editar inad_template.html, executar: python3 add_pdf_importer.py"
            ),
        },
        "database_schema": {
            "reports": "Relatórios históricos importados (report_name, report_date)",
            "clients": "Clientes inadimplentes por relatório (name, cpf_cnpj, cel, email)",
            "properties": "Imóveis do cliente (venda_id, identifier)",
            "parcels": "Parcelas em atraso (parcela, vencimento, vencimento_full, valor R$)",
            "action_logs": "Histórico de disparos WhatsApp (venda_id, client_name, sent_at)",
            "kpi_exclusions": "Clientes excluídos manualmente dos cálculos de KPI",
            "contact_outcomes": "Registros de desfechos de contatos (client_name, outcome, promised_date, next_contact, note)",
        },
        "api_endpoints": {
            "GET /api/context": "Este payload — contexto completo para IAs",
            "GET /api/health": "Status do servidor (porta, plataforma, Python)",
            "GET /api/reports": "Lista todos os relatórios importados",
            "GET /api/reports/<id>": "Árvore clientes/imóveis/parcelas de um relatório",
            "DELETE /api/reports/<id>": "Exclui relatório e dados relacionados (CASCADE)",
            "GET /api/clients": "Clientes do relatório mais recente",
            "GET /api/clients/all": "Lista única de nomes de clientes (todos os relatórios)",
            "POST /api/reports": "Importa novo relatório {report_name, report_date, clients}",
            "POST /api/clients": "Alias de POST /api/reports",
            "GET /api/sent": "Nomes de clientes que já receberam WhatsApp",
            "GET /api/actions/sent": "Alias de GET /api/sent",
            "POST /api/actions/sent": "Registra envio {venda_id, client_name} ou lista de nomes",
            "POST /api/sent": "Alias de POST /api/actions/sent",
            "GET /api/kpis": "KPIs de evolução e transições (?reports=1,2,3 opcional)",
            "GET /api/kpis/analytics": (
                "Série temporal segmentada novo/antigo para a página de Analytics "
                "(?start&end&reports&segment=all|novo|antigo&cutoff=YYYY-MM-DD|cutoff_last_n=N)"
            ),
            "GET /api/kpis/exclusions": "Clientes excluídos dos KPIs",
            "POST /api/kpis/exclusions": "Inclui/exclui cliente {client_name, exclude: bool}",
            "GET /api/queue": "Fila priorizada de inadimplência baseada em score de risco (?stage&min_days&limit)",
            "GET /api/clients/profile": "Dossiê completo do cliente com histórico de dívidas, contatos e desfechos (?name)",
            "POST /api/outcomes": "Insere desfecho de contato {client_name, outcome, venda_id, promised_date, next_contact, note}",
            "GET /api/outcomes": "Histórico de desfechos (?name&limit)",
            "DELETE /api/outcomes/<id>": "Exclui um registro de desfecho",
            "GET /api/worklist": "Alertas operacionais categorizados de recontato/promessas",
            "GET /api/summary": "Resumo executivo consolidado com metas de eficácia, aging e top devedores",
        },
        "business_rules": {
            "kpi_deduplication": (
                "Relatórios com a mesma report_date são deduplicados; "
                "mantém-se apenas o ID mais recente (is_duplicate=true nos demais)."
            ),
            "kpi_exclusions": (
                "Clientes em kpi_exclusions são ignorados em todos os cálculos de KPI."
            ),
            "recovery_rate": (
                "Taxa = clientes em R_n que NÃO aparecem em R_{n+1} / total em R_n × 100"
            ),
            "client_segmentation": (
                "Cliente é 'novo' se sua primeira aparição em qualquer relatório "
                "(por nome exato) ocorreu na data de corte ou depois; senão 'antigo'. "
                "Corte configurável por data (cutoff) ou N últimos relatórios "
                "(cutoff_last_n). Limitação: variações de grafia/acento no nome "
                "contam como clientes distintos."
            ),
            "risk_score_explainable": (
                "Score (0-100) = 45% valor normalizado + 35% envelhecimento da divida "
                "+ 20% reincidencia historica (transicoes False->True de presenca)."
            ),
            "billing_stages": (
                "Estágios definidos pela idade máxima do débito: "
                "0-30 dias (lembrete), 31-90 dias (firme), 91-120 dias (serio), >120 dias (pre_juridico)."
            ),
            "aging_reference_date_policy": (
                "Operacional (queue, worklist, profile, stages) calcula atraso a partir da data de hoje. "
                "Analítico (analytics, kpis) calcula atraso a partir da report_date para reprodutibilidade."
            ),
            "demo_mode": (
                "INAD_DEMO=1 (ou --demo) troca o banco para inad_demo.db, isolado "
                "do banco real; migrações/seed de dados reais não rodam em demo. "
                "Popular com: python3 generate_demo_data.py --reset"
            ),
            "offline_fallback": (
                "Se aberto via file://, dados vão para localStorage "
                "(inad_clients_db, inad_sent, inad_kpi_exclusions)."
            ),
            "privacy": (
                "Nunca commitar .db, .json com dados reais ou PDFs — ver .gitignore."
            ),
            "frontend_edit_rule": (
                "Editar apenas inad_template.html; regenerar inad_whatsapp.html via add_pdf_importer.py."
            ),
        },
        "live_stats": {
            "reports": report_count,
            "unique_clients": client_count,
            "clients_contacted": sent_count,
            "kpi_excluded_clients": excluded_count,
            "contact_outcomes": outcomes_count,
            "active_pre_juridico_clients": pre_juridico_count,
            "port": PORT,
            "platform": platform.system(),
            "demo": DEMO,
        },
        "ai_guidelines": [
            "Leia AI_CONTEXT.md antes de alterações significativas.",
            "Edite inad_template.html, nunca inad_whatsapp.html diretamente.",
            "Use sqlite3 nativo — sem ORMs ou drivers externos de banco.",
            "Preserve fallback localStorage para protocolo file://.",
            "Mantenha endpoints REST retrocompatíveis (/api/sent ↔ /api/actions/sent).",
        ],
        "markdown": ai_context_md,
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


def _is_port_open(port, timeout=0.35):
    """Testa se algo já está escutando em localhost:port (usado para não
    duplicar a instância demo ao clicar no botão várias vezes)."""
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except OSError:
        return False


def _launch_demo_instance():
    """Sobe (ou reaproveita) uma instância demo em DEMO_PORT, como processo
    filho independente, para o botão '🧪 Modo Demo' da UI."""
    if _is_port_open(DEMO_PORT):
        return {"already_running": True}

    env = os.environ.copy()
    env["INAD_DEMO"] = "1"
    env["INAD_PORT"] = str(DEMO_PORT)
    env["INAD_HEADLESS"] = "1"

    log_path = os.path.join(DIRECTORY, "inad_demo_server.log")
    log_file = open(log_path, "a", encoding="utf-8")

    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--demo", "--headless"],
        cwd=DIRECTORY, env=env, stdout=log_file, stderr=log_file, **kwargs,
    )
    return {"already_running": False}


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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        """
        Ponto de entrada para todas as requisições HTTP do tipo GET.
        Implementa um wrapper global de tratamento de erros para capturar qualquer exceção 
        e gravar o rastreamento completo (stack trace) no arquivo 'inad_errors.log'.
        """
        try:
            self._do_GET_unwrapped()
        except Exception as exc:
            import traceback
            logging.error(f"Erro não tratado em GET {self.path}: {exc}\n{traceback.format_exc()}")
            try:
                _json_response(self, {"error": "Internal Server Error", "details": str(exc)}, 500)
            except Exception:
                pass

    def _do_GET_unwrapped(self):
        """
        Executa o roteamento interno de requisições GET.
        Retorna arquivos estáticos (HTML/CSS/JS) ou responde a endpoints de API REST.
        """
        path = self.path.split("?")[0]

        # Atalhos de navegação: acessar a raiz ou caminhos amigáveis sempre
        # cai na página certa, em vez de listagem de diretório ou 404.
        if path in ("/", ""):
            target = "/inad_whatsapp.html" if os.path.exists(
                os.path.join(DIRECTORY, "inad_whatsapp.html")
            ) else "/inad_template.html"
            self._redirect(target)
        elif path in ("/kpi", "/kpis"):
            self._redirect("/inad_whatsapp.html#kpi")
        elif path in ("/cobranca", "/painel"):
            self._redirect("/inad_whatsapp.html#cobranca")
        elif path in ("/analytics", "/analitico"):
            self._redirect("/inad_analytics.html")

        elif path == "/api/reports":
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

        elif path == "/api/clients/all":
            cursor = get_conn().cursor()
            rows = cursor.execute("SELECT DISTINCT name FROM clients ORDER BY name").fetchall()
            _json_response(self, [r[0] for r in rows])

        elif path in ("/api/sent", "/api/actions/sent"):
            cursor = get_conn().cursor()
            names  = [r[0] for r in cursor.execute("""
                SELECT DISTINCT client_name FROM action_logs
                WHERE sent_at >= COALESCE((SELECT MAX(imported_at) FROM reports), '1970-01-01 00:00:00')
            """).fetchall()]
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

        elif path == "/api/kpis/analytics":
            from urllib.parse import parse_qs
            params = {}
            if "?" in self.path:
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                except Exception:
                    params = {}

            def _param(key):
                return params.get(key, [""])[0].strip() or None

            report_ids = None
            ids_str = _param("reports")
            if ids_str:
                try:
                    report_ids = [int(x) for x in ids_str.split(",")]
                except ValueError:
                    report_ids = None

            cutoff_last_n = None
            n_str = _param("cutoff_last_n")
            if n_str:
                try:
                    cutoff_last_n = int(n_str)
                except ValueError:
                    cutoff_last_n = None

            seg = _param("segment") or "all"
            if seg not in ("all", "novo", "antigo"):
                seg = "all"

            try:
                _json_response(self, get_analytics_data(
                    start=_param("start"),
                    end=_param("end"),
                    report_ids=report_ids,
                    segment=seg,
                    cutoff=_param("cutoff"),
                    cutoff_last_n=cutoff_last_n,
                ))
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        elif path == "/api/kpis/exclusions":
            cursor = get_conn().cursor()
            rows = cursor.execute("SELECT client_name FROM kpi_exclusions").fetchall()
            _json_response(self, [r[0] for r in rows])

        elif path == "/api/health":
            _json_response(self, {"status": "ok", "port": PORT,
                                   "platform": platform.system(),
                                   "python": platform.python_version(),
                                   "demo": DEMO, "db_file": DB_FILE})

        elif path == "/api/context":
            _json_response(self, get_system_context())

        elif path == "/api/queue":
            import datetime
            cursor = get_conn().cursor()
            ref_date = datetime.date.today().isoformat()
            
            from urllib.parse import parse_qs
            params = {}
            if "?" in self.path:
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                except Exception:
                    pass
                    
            def _param(key):
                return params.get(key, [""])[0].strip() or None
                
            stage_filter = _param("stage")
            min_days_filter = None
            if _param("min_days"):
                try:
                    min_days_filter = int(_param("min_days"))
                except ValueError:
                    pass
            limit_val = 50
            if _param("limit"):
                try:
                    limit_val = int(_param("limit"))
                except ValueError:
                    pass

            report_id = _dedup_latest_report_id(cursor)
            if not report_id:
                _json_response(self, {
                    "meta": {
                        "reference_date": ref_date,
                        "report_id": None,
                        "report_date": None,
                        "data_version": "0::"
                    },
                    "queue": []
                })
                return

            ver_row = cursor.execute(
                "SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(MAX(id), 0) FROM reports"
            ).fetchone()
            data_version = f"{ver_row[0]}:{ver_row[2]}:{ver_row[1]}"

            rep_date_row = cursor.execute(
                "SELECT COALESCE(NULLIF(report_date, ''), DATE(imported_at)) FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            report_date_str = rep_date_row[0] if rep_date_row else None

            cf_all = _client_financials(cursor, report_id, ref_date)
            if not cf_all:
                _json_response(self, {
                    "meta": {
                        "reference_date": ref_date,
                        "report_id": report_id,
                        "report_date": report_date_str,
                        "data_version": data_version
                    },
                    "queue": []
                })
                return

            vals = sorted([x["total_owed"] for x in cf_all.values()])
            idx = int(len(vals) * 0.9) if vals else 0
            p90 = vals[idx] if vals else 0.0

            reentries_map = _calculate_reentries(cursor)

            outcomes_rows = cursor.execute("""
                SELECT client_name, outcome, promised_date, next_contact, note, created_at
                FROM contact_outcomes
                WHERE (client_name, created_at) IN (
                    SELECT client_name, MAX(created_at) FROM contact_outcomes GROUP BY client_name
                )
            """).fetchall()
            latest_outcomes = {
                r[0]: {
                    "outcome": r[1], "promised_date": r[2], "next_contact": r[3], "note": r[4], "created_at": r[5]
                } for r in outcomes_rows
            }

            contact_rows = cursor.execute("""
                SELECT client_name, MAX(sent_at) FROM action_logs GROUP BY client_name
            """).fetchall()
            latest_contacts = {r[0]: r[1] for r in contact_rows}

            venda_rows = cursor.execute("""
                SELECT c.name, p.venda_id FROM properties p
                JOIN clients c ON p.client_id = c.id
                WHERE c.report_id = ?
            """, (report_id,)).fetchall()
            venda_map = {}
            for cname, vid in venda_rows:
                if cname not in venda_map:
                    venda_map[cname] = []
                venda_map[cname].append(vid)

            queue = []
            for name, cf in cf_all.items():
                reentries = reentries_map.get(name, {}).get("reentries", 0)
                score_info = _calculate_risk_score(cf["total_owed"], cf["max_days_overdue"], reentries, p90)
                
                bucket = _bucketize(cf["max_days_overdue"])
                stage = _stage_for_days(cf["max_days_overdue"])
                
                if stage_filter and stage != stage_filter:
                    continue
                if min_days_filter is not None and cf["max_days_overdue"] < min_days_filter:
                    continue

                last_c = latest_contacts.get(name)
                out_info = latest_outcomes.get(name)
                out_outcome = out_info["outcome"] if out_info else None
                out_date = out_info["created_at"] if out_info else None
                
                last_outcome = out_outcome
                last_outcome_date = out_date
                if last_c and (not out_date or last_c > out_date):
                    last_outcome = "sem_resposta"
                    last_outcome_date = last_c

                item = {
                    "name": name,
                    "cel": cf["cel"],
                    "email": cf["email"],
                    "venda_ids": venda_map.get(name, []),
                    "total_owed": cf["total_owed"],
                    "avg_parcel": cf["avg_parcel"],
                    "n_properties": cf["n_properties"],
                    "n_parcels": cf["n_parcels"],
                    "max_days_overdue": cf["max_days_overdue"],
                    "bucket": bucket,
                    "stage": stage,
                    "reentries": reentries,
                    "risk_score": score_info["score"],
                    "components": score_info["components"],
                    "last_contact": last_c,
                    "last_outcome": last_outcome,
                    "promised_date": out_info["promised_date"] if out_info else None,
                    "next_contact": out_info["next_contact"] if out_info else None,
                    "last_outcome_date": last_outcome_date
                }
                queue.append(item)

            queue.sort(key=lambda x: (-x["risk_score"], x["name"]))
            if limit_val > 0:
                queue = queue[:limit_val]

            _json_response(self, {
                "meta": {
                    "reference_date": ref_date,
                    "report_id": report_id,
                    "report_date": report_date_str,
                    "data_version": data_version
                },
                "queue": queue
            })

        elif path == "/api/clients/profile":
            from urllib.parse import parse_qs
            params = {}
            if "?" in self.path:
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                except Exception:
                    pass
            
            name = params.get("name", [""])[0].strip()
            if not name:
                _json_response(self, {"error": "Parametro name e obrigatorio"}, 400)
                return

            cursor = get_conn().cursor()
            exists = cursor.execute("SELECT 1 FROM clients WHERE name = ? LIMIT 1", (name,)).fetchone()
            if not exists:
                _json_response(self, {"error": f"Cliente '{name}' nao encontrado no sistema"}, 404)
                return

            import datetime
            ref_date = datetime.date.today().isoformat()
            report_id = _dedup_latest_report_id(cursor)
            
            latest_client_row = cursor.execute("""
                SELECT c.id, c.report_id, c.cpf_cnpj, c.cel, c.email
                FROM clients c
                JOIN reports r ON r.id = c.report_id
                WHERE c.name = ?
                ORDER BY COALESCE(NULLIF(r.report_date, ''), DATE(r.imported_at)) DESC, r.id DESC
                LIMIT 1
            """, (name,)).fetchone()
            
            c_id, c_rep_id, cpf_cnpj, cel, email = latest_client_row
            
            ver_row = cursor.execute(
                "SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(MAX(id), 0) FROM reports"
            ).fetchone()
            data_version = f"{ver_row[0]}:{ver_row[2]}:{ver_row[1]}"
            
            rep_date_str = None
            if report_id:
                rep_date_row = cursor.execute(
                    "SELECT COALESCE(NULLIF(report_date, ''), DATE(imported_at)) FROM reports WHERE id = ?", (report_id,)
                ).fetchone()
                rep_date_str = rep_date_row[0] if rep_date_row else None

            is_present_latest = cursor.execute("SELECT id FROM clients WHERE report_id = ? AND name = ?", (report_id, name)).fetchone() if report_id else None
            
            buckets_data = {
                "0-30": {"parcels": 0, "value": 0.0},
                "31-60": {"parcels": 0, "value": 0.0},
                "61-90": {"parcels": 0, "value": 0.0},
                "91-120": {"parcels": 0, "value": 0.0},
                "120+": {"parcels": 0, "value": 0.0}
            }
            properties_list = []
            
            if is_present_latest:
                latest_c_id = is_present_latest[0]
                props_rows = cursor.execute("""
                    SELECT id, venda_id, identifier FROM properties
                    WHERE client_id = ?
                """, (latest_c_id,)).fetchall()
                for p_id, p_vid, p_ident in props_rows:
                    parcels_list = []
                    parc_rows = cursor.execute("""
                        SELECT parcela, vencimento, vencimento_full, valor FROM parcels
                        WHERE property_id = ?
                    """, (p_id,)).fetchall()
                    for pa in parc_rows:
                        pa_val = round(pa[3], 2)
                        parcels_list.append({
                            "parcela": pa[0], "vencimento": pa[1], "vencimento_full": pa[2], "valor": pa_val
                        })
                        try:
                            v_dt = datetime.datetime.strptime(pa[2], "%Y-%m-%d").date()
                            ref_dt = datetime.datetime.strptime(ref_date, "%Y-%m-%d").date()
                            days = (ref_dt - v_dt).days
                            if days < 0:
                                days = 0
                            b = _bucketize(days)
                            buckets_data[b]["parcels"] += 1
                            buckets_data[b]["value"] = round(buckets_data[b]["value"] + pa_val, 2)
                        except Exception:
                            pass
                    properties_list.append({
                        "venda_id": p_vid, "identifier": p_ident, "parcels": parcels_list
                    })

            reentries_map = _calculate_reentries(cursor)
            rec_info = reentries_map.get(name, {
                "reentries": 0, "timeline": [], "first_seen": None, "currently_present": False
            })

            contacts_rows = cursor.execute("""
                SELECT sent_at, venda_id FROM action_logs
                WHERE client_name = ?
                ORDER BY sent_at DESC
            """, (name,)).fetchall()
            contacts_list = [{"sent_at": r[0], "venda_id": r[1]} for r in contacts_rows]

            outcomes_rows = cursor.execute("""
                SELECT id, outcome, promised_date, next_contact, note, created_at
                FROM contact_outcomes
                WHERE client_name = ?
                ORDER BY created_at DESC
            """, (name,)).fetchall()
            outcomes_list = [{
                "id": r[0], "outcome": r[1], "promised_date": r[2], "next_contact": r[3], "note": r[4], "created_at": r[5]
            } for r in outcomes_rows]

            contacted_times = len(contacts_list)
            regularized_after_contact = False
            if contacted_times > 0:
                last_contact_date = contacts_list[0]["sent_at"].split()[0]
                for t in rec_info["timeline"]:
                    if t["report_date"] > last_contact_date:
                        if not t["present"]:
                            regularized_after_contact = True
                        break
            days_since_last_contact = None
            if contacted_times > 0:
                last_contact_date = contacts_list[0]["sent_at"].split()[0]
                try:
                    l_dt = datetime.datetime.strptime(last_contact_date, "%Y-%m-%d").date()
                    ref_dt = datetime.datetime.strptime(ref_date, "%Y-%m-%d").date()
                    days_since_last_contact = (ref_dt - l_dt).days
                except Exception:
                    pass

            if is_present_latest:
                cf_all = _client_financials(cursor, report_id, ref_date)
                vals = sorted([x["total_owed"] for x in cf_all.values()])
                idx = int(len(vals) * 0.9) if vals else 0
                p90 = vals[idx] if vals else 0.0
                
                cf_client = cf_all.get(name, {
                    "total_owed": 0.0, "max_days_overdue": 0, "n_properties": 0, "n_parcels": 0, "oldest_due": None, "avg_parcel": 0.0
                })
                score_info = _calculate_risk_score(cf_client["total_owed"], cf_client["max_days_overdue"], rec_info["reentries"], p90)
                risk_data = {
                    "score": score_info["score"],
                    "components": score_info["components"],
                    "stage": _stage_for_days(cf_client["max_days_overdue"]),
                    "bucket": _bucketize(cf_client["max_days_overdue"])
                }
                financials_data = {
                    "total_owed": cf_client["total_owed"],
                    "avg_parcel": cf_client["avg_parcel"],
                    "n_properties": cf_client["n_properties"],
                    "n_parcels": cf_client["n_parcels"],
                    "oldest_due": cf_client["oldest_due"],
                    "max_days_overdue": cf_client["max_days_overdue"],
                    "buckets": buckets_data
                }
            else:
                risk_data = {
                    "score": 0.0,
                    "components": {"valor": 0.0, "aging": 0.0, "reincidencia": 0.0},
                    "stage": "regularizado",
                    "bucket": "-"
                }
                financials_data = {
                    "total_owed": 0.0,
                    "avg_parcel": 0.0,
                    "n_properties": 0,
                    "n_parcels": 0,
                    "oldest_due": None,
                    "max_days_overdue": 0,
                    "buckets": buckets_data
                }

            _json_response(self, {
                "meta": {
                    "reference_date": ref_date,
                    "report_id": report_id,
                    "report_date": rep_date_str,
                    "data_version": data_version
                },
                "name": name,
                "cel": cel or "",
                "email": email or "",
                "cpf_cnpj": cpf_cnpj or "",
                "financials": financials_data,
                "risk": risk_data,
                "recurrence": {
                    "first_seen": rec_info["first_seen"],
                    "reentries": rec_info["reentries"],
                    "currently_present": rec_info["currently_present"],
                    "timeline": rec_info["timeline"]
                },
                "contacts": contacts_list,
                "outcomes": outcomes_list,
                "response_behavior": {
                    "contacted_times": contacted_times,
                    "regularized_after_contact": regularized_after_contact,
                    "days_since_last_contact": days_since_last_contact
                },
                "properties": properties_list
            })

        elif path == "/api/outcomes":
            from urllib.parse import parse_qs
            params = {}
            if "?" in self.path:
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                except Exception:
                    pass
            cname = params.get("name", [""])[0].strip()
            limit_val = 100
            if params.get("limit"):
                try:
                    limit_val = int(params.get("limit")[0])
                except ValueError:
                    pass
                    
            cursor = get_conn().cursor()
            if cname:
                rows = cursor.execute("""
                    SELECT id, client_name, venda_id, action_log_id, outcome, promised_date, next_contact, note, created_at
                    FROM contact_outcomes
                    WHERE client_name = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (cname, limit_val)).fetchall()
            else:
                rows = cursor.execute("""
                    SELECT id, client_name, venda_id, action_log_id, outcome, promised_date, next_contact, note, created_at
                    FROM contact_outcomes
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit_val,)).fetchall()
                
            res = [{
                "id": r[0], "client_name": r[1], "venda_id": r[2], "action_log_id": r[3],
                "outcome": r[4], "promised_date": r[5], "next_contact": r[6], "note": r[7], "created_at": r[8]
            } for r in rows]
            _json_response(self, res)

        elif path == "/api/worklist":
            import datetime
            cursor = get_conn().cursor()
            ref_date = datetime.date.today().isoformat()
            
            report_id = _dedup_latest_report_id(cursor)
            if not report_id:
                _json_response(self, {
                    "meta": {"reference_date": ref_date, "report_id": None, "report_date": None, "data_version": "0::"},
                    "promessas_vencidas": [], "recontato_agendado": [], "sem_resposta": [], "novos_pre_juridico": []
                })
                return

            ver_row = cursor.execute(
                "SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(MAX(id), 0) FROM reports"
            ).fetchone()
            data_version = f"{ver_row[0]}:{ver_row[2]}:{ver_row[1]}"

            rep_date_row = cursor.execute(
                "SELECT COALESCE(NULLIF(report_date, ''), DATE(imported_at)) FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            report_date_str = rep_date_row[0] if rep_date_row else None

            w_data = _get_worklist_data(cursor, ref_date)
            _json_response(self, {
                "meta": {
                    "reference_date": ref_date,
                    "report_id": report_id,
                    "report_date": report_date_str,
                    "data_version": data_version
                },
                "promessas_vencidas": w_data["promessas_vencidas"],
                "recontato_agendado": w_data["recontato_agendado"],
                "sem_resposta": w_data["sem_resposta"],
                "novos_pre_juridico": w_data["novos_pre_juridico"]
            })

        elif path == "/api/summary":
            import datetime
            cursor = get_conn().cursor()
            ref_date = datetime.date.today().isoformat()
            
            report_id = _dedup_latest_report_id(cursor)
            if not report_id:
                _json_response(self, {
                    "meta": {"reference_date": ref_date, "report_id": None, "report_date": None, "data_version": "0::"},
                    "current": {"clients": 0, "total_owed": 0.0, "avg_days_overdue": 0},
                    "trend": {"vs_previous_report": {"clients_delta": 0, "value_delta": 0.0, "direction": "estavel"}},
                    "aging_distribution": {},
                    "pre_juridico": {"count": 0, "value": 0.0, "new_this_report": 0},
                    "top_debtors": [],
                    "effectiveness": {"contacted": 0, "regularized_after_contact": 0, "rate": 0.0, "promises_made": 0, "promises_kept": 0},
                    "worklist_counts": {"promessas_vencidas": 0, "sem_resposta": 0, "novos_pre_juridico": 0}
                })
                return

            ver_row = cursor.execute(
                "SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(MAX(id), 0) FROM reports"
            ).fetchone()
            data_version = f"{ver_row[0]}:{ver_row[2]}:{ver_row[1]}"

            rep_date_row = cursor.execute(
                "SELECT COALESCE(NULLIF(report_date, ''), DATE(imported_at)) FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
            report_date_str = rep_date_row[0] if rep_date_row else None

            cf_all = _client_financials(cursor, report_id, ref_date)
            total_clients = len(cf_all)
            total_value = round(sum(x["total_owed"] for x in cf_all.values()), 2)
            avg_days = int(sum(x["max_days_overdue"] for x in cf_all.values()) / total_clients) if total_clients > 0 else 0

            report_rows = cursor.execute("""
                SELECT id, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) AS rdate
                FROM   reports
                ORDER  BY rdate DESC, id DESC
            """).fetchall()
            seen_dates = set()
            latest_ids = []
            for rid, rdate in report_rows:
                if rdate not in seen_dates:
                    seen_dates.add(rdate)
                    latest_ids.append((rid, rdate))
            
            clients_delta = 0
            value_delta = 0.0
            direction = "estavel"
            if len(latest_ids) > 1:
                prev_id = latest_ids[1][0]
                prev_date = latest_ids[1][1]
                prev_cf = _client_financials(cursor, prev_id, prev_date)
                prev_clients = len(prev_cf)
                prev_value = sum(x["total_owed"] for x in prev_cf.values())
                
                clients_delta = total_clients - prev_clients
                value_delta = round(total_value - prev_value, 2)
                direction = "melhora" if value_delta < 0 else "piora" if value_delta > 0 else "estavel"

            aging_distribution = {
                "0-30": {"clients": 0, "value": 0.0},
                "31-60": {"clients": 0, "value": 0.0},
                "61-90": {"clients": 0, "value": 0.0},
                "91-120": {"clients": 0, "value": 0.0},
                "120+": {"clients": 0, "value": 0.0}
            }
            pre_juridico_count = 0
            pre_juridico_value = 0.0
            for cf in cf_all.values():
                b = _bucketize(cf["max_days_overdue"])
                aging_distribution[b]["clients"] += 1
                aging_distribution[b]["value"] = round(aging_distribution[b]["value"] + cf["total_owed"], 2)
                if cf["max_days_overdue"] > 120:
                    pre_juridico_count += 1
                    pre_juridico_value = round(pre_juridico_value + cf["total_owed"], 2)

            top_val = 5
            if "?" in self.path:
                from urllib.parse import parse_qs
                try:
                    params = parse_qs(self.path.split("?", 1)[1])
                    top_val = int(params.get("top", [5])[0])
                except Exception:
                    pass
            top_debtors = sorted(
                [{"name": x["name"], "total_owed": x["total_owed"], "max_days_overdue": x["max_days_overdue"], "stage": _stage_for_days(x["max_days_overdue"])} for x in cf_all.values()],
                key=lambda x: x["total_owed"],
                reverse=True
            )[:top_val]

            eff_data = _contact_effectiveness(cursor)

            w_data = _get_worklist_data(cursor, ref_date)
            w_counts = {
                "promessas_vencidas": len(w_data["promessas_vencidas"]),
                "recontato_agendado": len(w_data["recontato_agendado"]),
                "sem_resposta": len(w_data["sem_resposta"]),
                "novos_pre_juridico": len(w_data["novos_pre_juridico"])
            }

            _json_response(self, {
                "meta": {
                    "reference_date": ref_date,
                    "report_id": report_id,
                    "report_date": report_date_str,
                    "data_version": data_version
                },
                "current": {"clients": total_clients, "total_owed": total_value, "avg_days_overdue": avg_days},
                "trend": {"vs_previous_report": {"clients_delta": clients_delta, "value_delta": value_delta, "direction": direction}},
                "aging_distribution": aging_distribution,
                "pre_juridico": {"count": pre_juridico_count, "value": pre_juridico_value, "new_this_report": w_counts["novos_pre_juridico"]},
                "top_debtors": top_debtors,
                "effectiveness": eff_data,
                "worklist_counts": w_counts
            })

        else:
            super().do_GET()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        """
        Ponto de entrada para requisições POST.
        Implementa um wrapper global de tratamento de erros que registra falhas críticas no log persistente.
        """
        try:
            self._do_POST_unwrapped()
        except Exception as exc:
            import traceback
            logging.error(f"Erro não tratado em POST {self.path}: {exc}\n{traceback.format_exc()}")
            try:
                _json_response(self, {"error": "Internal Server Error", "details": str(exc)}, 500)
            except Exception:
                pass

    def _do_POST_unwrapped(self):
        """
        Executa as ações de alteração e inserção de dados do painel,
        como gravação de relatórios de cobrança e cadastro de desfechos de contato.
        """
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
                print(f"[API] Novo relatório importado com sucesso: '{report_name}' (ID: {report_id})")
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
                            print(f"[API] Contato registrado: '{name}' (via lista)")
                else:
                    vid  = payload.get("venda_id", "0000")
                    name = payload.get("client_name", "")
                    if name:
                        cursor.execute(
                            "INSERT INTO action_logs (venda_id, client_name) VALUES (?,?)",
                            (vid, name),
                        )
                        print(f"[API] Contato registrado: '{name}' para a venda '{vid}'")
                conn.commit()
                _json_response(self, {"status": "success"})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        elif path == "/api/demo/launch":
            if DEMO:
                _json_response(self, {"error": "Este servidor já está em modo demo."}, 400)
                return
            try:
                info = _launch_demo_instance()
                _json_response(self, {
                    "status": "success",
                    "port": DEMO_PORT,
                    "url": f"http://localhost:{DEMO_PORT}/inad_whatsapp.html",
                    "health_url": f"http://localhost:{DEMO_PORT}/api/health",
                    "already_running": info["already_running"],
                })
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        elif path == "/api/kpis/exclusions":
            try:
                conn = get_conn()
                cursor = conn.cursor()
                client_name = payload.get("client_name")
                exclude = payload.get("exclude", True)

                if not client_name:
                    _json_response(self, {"error": "Nome do cliente ausente"}, 400)
                    return

                if exclude:
                    cursor.execute("INSERT OR IGNORE INTO kpi_exclusions (client_name) VALUES (?)", (client_name,))
                else:
                    cursor.execute("DELETE FROM kpi_exclusions WHERE client_name = ?", (client_name,))

                conn.commit()
                _json_response(self, {"status": "success"})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        elif path == "/api/outcomes":
            try:
                conn = get_conn()
                cursor = conn.cursor()
                client_name = payload.get("client_name")
                outcome = payload.get("outcome")
                venda_id = payload.get("venda_id", "")
                action_log_id = payload.get("action_log_id")
                promised_date = payload.get("promised_date")
                next_contact = payload.get("next_contact")
                note = payload.get("note", "")

                if not client_name:
                    _json_response(self, {"error": "client_name e obrigatorio"}, 400)
                    return
                if not outcome:
                    _json_response(self, {"error": "outcome e obrigatorio"}, 400)
                    return
                if outcome not in OUTCOME_TYPES:
                    _json_response(self, {"error": f"outcome deve ser um dos: {OUTCOME_TYPES}"}, 400)
                    return
                if outcome == "prometeu_pagar" and not promised_date:
                    _json_response(self, {"error": "promised_date e obrigatoria para desfecho prometeu_pagar"}, 400)
                    return

                cursor.execute("""
                    INSERT INTO contact_outcomes (client_name, venda_id, action_log_id, outcome, promised_date, next_contact, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (client_name, venda_id, action_log_id, outcome, promised_date, next_contact, note))
                conn.commit()
                print(f"[API] Desfecho registrado: '{outcome}' para o cliente '{client_name}'")
                _json_response(self, {"status": "success", "id": cursor.lastrowid})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, 500)

        else:
            _json_response(self, {"error": "Rota não encontrada"}, 404)

    # ── DELETE ────────────────────────────────────────────────────────────────
    def do_DELETE(self):
        """
        Ponto de entrada para requisições DELETE.
        Exclui relatórios ou desfechos de contato, registrando qualquer falha no log persistente.
        """
        try:
            self._do_DELETE_unwrapped()
        except Exception as exc:
            import traceback
            logging.error(f"Erro não tratado em DELETE {self.path}: {exc}\n{traceback.format_exc()}")
            try:
                _json_response(self, {"error": "Internal Server Error", "details": str(exc)}, 500)
            except Exception:
                pass

    def _do_DELETE_unwrapped(self):
        """
        Executa a deleção física ou lógica de relatórios e desfechos de contatos.
        """
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
        elif path.startswith("/api/outcomes/"):
            try:
                oid = int(path.rsplit("/", 1)[-1])
                conn = get_conn()
                cursor = conn.cursor()
                exists = cursor.execute("SELECT 1 FROM contact_outcomes WHERE id = ? LIMIT 1", (oid,)).fetchone()
                if not exists:
                    _json_response(self, {"error": "Desfecho não encontrado"}, 404)
                    return
                cursor.execute("DELETE FROM contact_outcomes WHERE id = ?", (oid,))
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
    allow_reuse_address = (platform.system() != "Windows")

    def server_bind(self):
        if platform.system() == "Windows":
            import socket
            try:
                # SO_EXCLUSIVEADDRUSE evita que outra aplicação roube a porta no Windows
                self.socket.setsockopt(
                    socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 14), 1
                )
            except OSError:
                pass
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
    if DEMO:
        print(f"  ⚠ DEMO     : Banco isolado ({DB_FILE}) — dados fictícios")
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
