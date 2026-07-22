"""
INAD — Painel de Cobrança
Servidor local multiplataforma (macOS, Windows, Linux/Servidor)

Uso:
  python3 run.py                    → Inicia na porta 8000 e abre o navegador
  INAD_PORT=9090 python3 run.py     → Usa a porta 9090
  INAD_HEADLESS=1 python3 run.py    → Modo servidor (sem abrir o navegador)
  python3 run.py --headless         → Igual ao modo servidor

Padrão: bind em 127.0.0.1 (só local), sem autenticação. Para expor na rede:
  INAD_HOST=0.0.0.0 python3 run.py --add-operator "Nome"   → cadastra operador (uma vez)
  python3 run.py --add-operator "Nome" --read-only         → operador só-leitura (POST/DELETE = 403)
  INAD_HOST=0.0.0.0 python3 run.py                          → sobe exigindo token
  python3 run.py --list-operators / --revoke-operator "Nome"
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
import re
import hashlib
import hmac
import secrets
import unicodedata
import datetime
import urllib.request
import urllib.parse
import urllib.error

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

# Endereço de bind. Padrão 127.0.0.1 (só local) — expor na rede é opt-in
# explícito via INAD_HOST/--host, e exige operadores cadastrados (ver
# _authenticate() e --add-operator mais abaixo).
HOST = os.environ.get("INAD_HOST", "127.0.0.1")
for _i, _arg in enumerate(sys.argv):
    if _arg == "--host" and _i + 1 < len(sys.argv):
        HOST = sys.argv[_i + 1]
    elif _arg.startswith("--host="):
        HOST = _arg.split("=", 1)[1]


def _is_loopback_bind():
    return HOST in ("127.0.0.1", "localhost", "::1")


# Pasta base do projeto. Em execução normal (`python run.py`), é a pasta do
# próprio script. Num executável empacotado via PyInstaller `--onefile`,
# `__file__` resolve para dentro da pasta TEMPORÁRIA de extração do
# bootloader — que é apagada quando o processo termina. Sem este desvio,
# o banco de dados (e tudo mais salvo via DIRECTORY) seria recriado do zero
# a cada execução do .exe. `sys.executable` sempre aponta pra pasta real do
# executável, mesmo em modo onefile — por isso é usado quando `sys.frozen`.
if getattr(sys, "frozen", False):
    DIRECTORY = os.path.dirname(os.path.abspath(sys.executable))
else:
    DIRECTORY = os.path.dirname(os.path.abspath(__file__))

env_path = os.path.join(DIRECTORY, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

# Teto de tamanho do corpo de requisições POST (proteção contra DoS por corpo
# gigante). Configurável via INAD_MAX_BODY_BYTES; padrão 20 MB.
MAX_BODY_BYTES = int(os.environ.get("INAD_MAX_BODY_BYTES", 20 * 1024 * 1024))

# Teto de itens retornados por endpoints paginados (limit/top), independente
# do que o cliente pedir na query string.
MAX_RESULT_LIMIT = 500

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

DB_FILE   = "inad_database.db"
DB_PATH   = os.path.join(DIRECTORY, DB_FILE)

# Modo headless: ativado via arg --headless, var INAD_HEADLESS=1,
# ou quando o sistema não tiver display (servidores Linux sem GUI).
_headless_env  = os.environ.get("INAD_HEADLESS", "0").strip() == "1"
_headless_arg  = "--headless" in sys.argv
_no_display    = (platform.system() == "Linux"
                  and not os.environ.get("DISPLAY")
                  and not os.environ.get("WAYLAND_DISPLAY"))
HEADLESS = _headless_env or _headless_arg or _no_display

# ─── REGRAS DE NEGÓCIO E CONSTANTES OPERACIONAIS (v3.0.0) ────────────────────
AGING_BUCKETS = [(0, 30, '0-30'), (31, 60, '31-60'), (61, 90, '61-90'), (91, 120, '91-120'), (121, None, '121+')]
PREJURIDICO_DAYS = 120  # pré-jurídico dispara com dias de atraso > PREJURIDICO_DAYS (ou seja, a partir de 121)
STAGES = {'0-30': 'lembrete', '31-60': 'firme', '61-90': 'firme', '91-120': 'serio', '121+': 'pre_juridico'}
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


def _normalize_name(name):
    """Chave normalizada para casar a IDENTIDADE de um cliente entre
    relatórios/exclusões/desfechos: remove acentos, colapsa espaços e
    uniformiza caixa. Nunca usada para exibição — o `name`/`client_name`
    original (como veio do PDF) é sempre preservado nas colunas; isto só
    entra em comparações (JOIN/WHERE/GROUP BY) via normalize_name() no SQL.
    Abreviações (\"Ma.\" vs \"Maria\") ficam fora do escopo — fora de alcance
    de uma normalização puramente textual."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\s+", " ", n).strip()
    return n.upper()


def get_conn():
    """Retorna a conexão SQLite da thread atual, criando uma nova se necessário."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.create_function("normalize_name", 1, _normalize_name)
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
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id           INTEGER NOT NULL,
            name                TEXT    NOT NULL,
            cpf_cnpj            TEXT    DEFAULT '',
            cel                 TEXT    DEFAULT '',
            email               TEXT    DEFAULT '',
            endereco            TEXT    DEFAULT '',
            telefone_secundario TEXT    DEFAULT '',
            FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS properties (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id      INTEGER NOT NULL,
            venda_id       TEXT    NOT NULL,
            identifier     TEXT    NOT NULL,
            empreendimento TEXT    DEFAULT '',
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS parcels (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id            INTEGER NOT NULL,
            parcela                TEXT    NOT NULL,
            vencimento             TEXT    NOT NULL,
            vencimento_full        TEXT    NOT NULL,
            valor                  REAL    DEFAULT 0.0,
            valor_centavos         INTEGER DEFAULT 0,
            valor_original_centavos INTEGER DEFAULT 0,
            valor_juros_centavos    INTEGER DEFAULT 0,
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

        -- 8. Operadores (autenticação mínima — só relevante quando exposto
        -- além de localhost; ver _authenticate()/--add-operator)
        CREATE TABLE IF NOT EXISTS operators (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            token_hash TEXT    NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active     INTEGER NOT NULL DEFAULT 1,
            -- Papel de escrita. can_write=0 → operador somente-leitura: é
            -- autenticado normalmente e pode fazer GET, mas POST/DELETE
            -- retornam 403 (ver _authenticate()/do_POST/do_DELETE).
            can_write  INTEGER NOT NULL DEFAULT 1
        );

        -- 9. Auditoria de acesso a PII individual (S6) — quem consultou o
        -- perfil (CPF/telefone/endereço) de qual cliente e quando. Só
        -- alimentada por leituras que expõem PII de UM cliente específico
        -- (GET /api/clients/profile) — ver _log_access().
        CREATE TABLE IF NOT EXISTS access_audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            operator    TEXT,
            client_name TEXT    NOT NULL,
            accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_clients_name         ON clients(name);
        CREATE INDEX IF NOT EXISTS idx_clients_report_id    ON clients(report_id);
        CREATE INDEX IF NOT EXISTS idx_properties_client_id ON properties(client_id);
        CREATE INDEX IF NOT EXISTS idx_parcels_property_id  ON parcels(property_id);
        CREATE INDEX IF NOT EXISTS idx_parcels_venc         ON parcels(vencimento_full);
        CREATE INDEX IF NOT EXISTS idx_outcomes_client      ON contact_outcomes(client_name);
        CREATE INDEX IF NOT EXISTS idx_outcomes_created     ON contact_outcomes(created_at);
        CREATE INDEX IF NOT EXISTS idx_access_audit_client   ON access_audit(client_name);
        CREATE INDEX IF NOT EXISTS idx_access_audit_accessed ON access_audit(accessed_at);
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

    # Migração K7: adiciona valor_centavos (INTEIRO) e faz backfill a partir
    # de valor (R$ → centavos, ROUND(valor*100)). Todo SUM/AVG monetário do
    # sistema passa a usar valor_centavos (soma de inteiros não tem drift de
    # ponto flutuante) — valor (REAL) continua existindo só para exibição do
    # valor individual de UMA parcela, nunca mais usado em agregações.
    if "valor_centavos" not in existing_parcel_cols:
        cursor.execute("ALTER TABLE parcels ADD COLUMN valor_centavos INTEGER DEFAULT 0")
        cursor.execute("UPDATE parcels SET valor_centavos = CAST(ROUND(valor * 100) AS INTEGER)")
        print("[MIGRAÇÃO] Coluna valor_centavos adicionada e populada a partir de valor (R$ → centavos).")

    # Migração: papel somente-leitura de operador (can_write). A coluna nasce
    # com DEFAULT 1, então todo operador já cadastrado num banco legado
    # continua com acesso de escrita — nenhuma mudança de comportamento na
    # migração; o papel restrito só existe para operadores criados de
    # propósito com --add-operator ... --read-only.
    existing_operator_cols = {row[1] for row in cursor.execute("PRAGMA table_info(operators)")}
    if "can_write" not in existing_operator_cols:
        cursor.execute("ALTER TABLE operators ADD COLUMN can_write INTEGER NOT NULL DEFAULT 1")
        print("[MIGRAÇÃO] Coluna can_write adicionada à tabela operators (papel somente-leitura).")

    # Migração UAU API: Novas colunas de metadados ricos para bancos legados
    existing_clients_cols = {row[1] for row in cursor.execute("PRAGMA table_info(clients)")}
    if "endereco" not in existing_clients_cols:
        cursor.execute("ALTER TABLE clients ADD COLUMN endereco TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE clients ADD COLUMN telefone_secundario TEXT DEFAULT ''")
    
    existing_props_cols = {row[1] for row in cursor.execute("PRAGMA table_info(properties)")}
    if "empreendimento" not in existing_props_cols:
        cursor.execute("ALTER TABLE properties ADD COLUMN empreendimento TEXT DEFAULT ''")
        
    if "valor_original_centavos" not in existing_parcel_cols:
        cursor.execute("ALTER TABLE parcels ADD COLUMN valor_original_centavos INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE parcels ADD COLUMN valor_juros_centavos INTEGER DEFAULT 0")
        cursor.execute("UPDATE parcels SET valor_original_centavos = valor_centavos")

    conn.commit()

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
                    updates.append((val, round(val * 100), pa_id))

            if updates:
                cursor.executemany("UPDATE parcels SET valor = ?, valor_centavos = ? WHERE id = ?", updates)
                conn.commit()
                print(f"[MIGRAÇÃO] {len(updates)} parcelas atualizadas com o valor real.")
    except Exception as exc:
        print(f"[MIGRAÇÃO] Erro no backfill de valores de parcelas: {exc}")

    _apply_kpi_exclusions_seed(cursor, conn)


def _apply_kpi_exclusions_seed(cursor, conn):
    """Carrega exclusões padrão de kpi_exclusions.json, se existir localmente
    (arquivo não versionado no Git — pode conter nomes reais de clientes)."""
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


# ─── OPERADORES (autenticação mínima para exposição em rede) ─────────────────
# Autenticação só é exigida quando o servidor não está em bind loopback (ver
# _is_loopback_bind()/_authenticate() mais abaixo). Cada operador tem seu
# próprio token — nunca armazenado em claro, só o hash SHA-256 dele.

def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _add_operator(name, can_write=True):
    """Cria um operador novo com um token aleatório. Retorna o token em claro
    (só existe neste retorno — não fica gravado em lugar nenhum).
    can_write=False cria um operador somente-leitura (pode GET, mas POST/DELETE
    retornam 403 — ver _authenticate())."""
    name = name.strip()
    if not name:
        raise ValueError("Nome do operador não pode ser vazio.")
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO operators (name, token_hash, active, can_write) VALUES (?, ?, 1, ?)",
        (name, _hash_token(token), 1 if can_write else 0),
    )
    conn.commit()
    return token


def _list_operators():
    rows = get_conn().cursor().execute(
        "SELECT name, created_at, active, can_write FROM operators ORDER BY created_at"
    ).fetchall()
    return [
        {"name": r[0], "created_at": r[1], "active": bool(r[2]), "can_write": bool(r[3])}
        for r in rows
    ]


def _revoke_operator(name):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE operators SET active = 0 WHERE name = ?", (name.strip(),))
    conn.commit()
    return cursor.rowcount > 0


def _has_active_operators():
    row = get_conn().cursor().execute(
        "SELECT 1 FROM operators WHERE active = 1 LIMIT 1"
    ).fetchone()
    return row is not None


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


_DATE_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_DATE_BR_RE  = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')


def _normalize_date(value):
    """Normaliza uma data para ISO (AAAA-MM-DD).
    Aceita ISO (AAAA-MM-DD) ou BR (DD/MM/AAAA) e converte para ISO.
    Valores vazios/None passam adiante sem alteração (ausência de data
    conhecida, não é um formato desconhecido). Levanta ValueError com
    mensagem explícita para qualquer outro formato ou data inexistente
    no calendário (ex.: 31/02) — nunca grava silenciosamente uma data
    não confiável, já que comparações/ordenação e julianday() no resto
    do sistema assumem ISO."""
    if not value:
        return value
    value = value.strip()
    if _DATE_ISO_RE.match(value):
        candidate = value
    else:
        m = _DATE_BR_RE.match(value)
        if not m:
            raise ValueError(f"Data inválida: {value!r} (use AAAA-MM-DD ou DD/MM/AAAA)")
        d, mo, y = m.groups()
        candidate = f"{y}-{mo}-{d}"
    import datetime
    try:
        datetime.date.fromisoformat(candidate)
    except ValueError:
        raise ValueError(f"Data inválida: {value!r} (use AAAA-MM-DD ou DD/MM/AAAA)")
    return candidate


def _cents_to_reais(cents):
    """Converte centavos inteiros (base exata de soma/agregação, K7) para
    reais (float) — só na apresentação. Nunca somar o resultado desta função
    de novo; some `valor_centavos` (inteiro) e converta uma única vez ao final."""
    return round((cents or 0) / 100.0, 2)


def _insert_clients(cursor, report_id, clients):
    """Insere em batch todos os clientes, imóveis e parcelas de um relatório."""
    for c_name, c_data in clients.items():
        cursor.execute(
            "INSERT INTO clients (report_id, name, cpf_cnpj, cel, email, endereco, telefone_secundario) VALUES (?,?,?,?,?,?,?)",
            (report_id, c_name,
             c_data.get("cpf_cnpj", ""), c_data.get("cel", ""), c_data.get("email", ""),
             c_data.get("endereco", ""), c_data.get("telefone_secundario", "")),
        )
        client_id = cursor.lastrowid
        for prop in c_data.get("properties", []):
            cursor.execute(
                "INSERT INTO properties (client_id, venda_id, identifier, empreendimento) VALUES (?,?,?,?)",
                (client_id, prop.get("venda_id", ""), prop.get("identifier", ""), prop.get("empreendimento", "")),
            )
            property_id = cursor.lastrowid
            for parc in prop.get("parcels", []):
                valor_atualizado = float(parc.get("valor") or parc.get("valor_total") or parc.get("valor_parcela") or 0.0)
                valor_original = float(parc.get("valor_original", valor_atualizado))
                valor_juros = float(parc.get("valor_juros", 0.0))
                cursor.execute(
                    "INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full, valor, valor_centavos, valor_original_centavos, valor_juros_centavos) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (property_id, parc.get("parcela", ""),
                     parc.get("vencimento", ""), _normalize_date(parc.get("vencimento_full", "")),
                     valor_atualizado, round(valor_atualizado * 100), round(valor_original * 100), round(valor_juros * 100)),
                )


# ─── QUERIES DE DADOS ─────────────────────────────────────────────────────────

def get_clients_for_report(report_id):
    """Retorna a árvore de clientes/imóveis/parcelas de um relatório como dict."""
    cursor = get_conn().cursor()
    rows = cursor.execute("""
        SELECT c.name, c.cpf_cnpj, c.cel, c.email, c.endereco, c.telefone_secundario,
               p.venda_id, p.identifier, p.empreendimento,
               pa.parcela, pa.vencimento, pa.vencimento_full,
               COALESCE(pa.valor, 0.0), pa.valor_original_centavos, pa.valor_juros_centavos
        FROM   clients c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  c.report_id = ?
        ORDER  BY c.name, p.venda_id, pa.parcela
    """, (report_id,)).fetchall()

    result = {}
    for row in rows:
        c_name, c_cpf, c_cel, c_email, c_end, c_tel2, p_vid, p_ident, p_emp, pa_num, pa_venc, pa_venc_f, pa_val, pa_vo_cents, pa_vj_cents = row
        if not c_name:
            continue
        if c_name not in result:
            result[c_name] = {"name": c_name, "cpf_cnpj": c_cpf,
                               "cel": c_cel, "email": c_email,
                               "endereco": c_end, "telefone_secundario": c_tel2, "properties": []}
        props = result[c_name]["properties"]
        prop  = next((x for x in props if x["venda_id"] == p_vid), None) if p_vid else None
        if p_vid and not prop:
            prop = {"venda_id": p_vid, "identifier": p_ident, "empreendimento": p_emp, "parcels": []}
            props.append(prop)
        if prop and pa_num:
            prop["parcels"].append({
                "parcela": pa_num, "vencimento": pa_venc, "vencimento_full": pa_venc_f, 
                "valor": pa_val, "valor_original": _cents_to_reais(pa_vo_cents), "valor_juros": _cents_to_reais(pa_vj_cents)
            })
    return result


# ─── Integração com a API do ERP UAU (SOMENTE LEITURA) ──────────────────────
# Regras (ver memória uau-sync-implementation-rules): só endpoints de CONSULTA,
# enumeração via ConsultarPessoasComVenda com filtro empresa/obra, sem fan-out
# pesado de Pessoas/*. Nada de escrita (LGPD / operador read-only).
UAU_HTTP_TIMEOUT = int(os.environ.get("UAU_HTTP_TIMEOUT", "30"))


def _uau_first(d, keys):
    """Primeiro valor não-vazio dentre `keys` num dict (tolerante a variações de caixa)."""
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) not in (None, ""):
                return d[k]
    return None


def _uau_as_list(v):
    """Normaliza a resposta da UAU (que às vezes envolve a lista numa chave) para lista."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        for k in ("Pessoas", "pessoas", "Result", "result", "Dados", "dados",
                  "Items", "items", "Clientes", "clientes"):
            if isinstance(v.get(k), list):
                return v[k]
        return [v]
    return []


def _uau_parse_date(s):
    """Converte data da UAU (ISO date-time ou dd/mm/yyyy) em datetime.date, ou None."""
    if not s:
        return None
    s = str(s)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _uau_request(base, version, endpoint, integ_token, auth_token=None,
                 payload=None, query=None):
    """POST autenticado à API UAU. Retorna o corpo decodificado (dict/list) ou a
    string crua. A UAU às vezes serializa JSON dentro de string — desembrulha uma vez."""
    url = f"{base.rstrip('/')}/api/v{version}{endpoint}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(payload if payload is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-INTEGRATION-Authorization", integ_token)
    if auth_token:
        req.add_header("Authorization", auth_token)
    with urllib.request.urlopen(req, timeout=UAU_HTTP_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8").strip()
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _uau_parse_recebiveis(receb):
    """RecebiveisResponse (Vendas → ParcelasVenda) → árvore de imóveis/parcelas do
    INAD, mantendo só parcelas VENCIDAS (inadimplência = vencimento < hoje)."""
    hoje = datetime.date.today()
    props = []
    if not isinstance(receb, dict):
        return props
    for venda in receb.get("Vendas") or []:
        empreend = venda.get("Obra") or ""
        venda_id = str(venda.get("Venda", "") or "")
        itens = venda.get("ItensVenda") or []
        identifier = ""
        if itens:
            identifier = itens[0].get("Identificador") or itens[0].get("DescProduto") or ""
        parcels = []
        for pv in venda.get("ParcelasVenda") or []:
            venc = _uau_parse_date(pv.get("DataVencimento"))
            if venc is None or venc >= hoje:
                continue  # só parcelas vencidas
            valor = float(pv.get("ValorParcela") or 0.0)
            parcels.append({
                "parcela": str(pv.get("NumParcela", "") or ""),
                "vencimento": venc.strftime("%d/%m"),
                "vencimento_full": venc.isoformat(),
                "valor": valor,
                "valor_original": valor,
                "valor_juros": 0.0,
            })
        if parcels:
            props.append({
                "venda_id": venda_id, "identifier": identifier,
                "empreendimento": empreend, "parcels": parcels,
            })
    return props


def _sync_from_uau(empresa=None, obra=None):
    """Consulta a API UAU (read-only) e devolve a árvore `clients` pronta para
    _insert_clients. Fluxo documentado: Autenticar → ConsultarPessoasComVenda
    (filtro empresa/obra) → por CPF: ParcelasECobrancasDoCliente → inadimplência."""
    base = os.environ["UAU_BASE_URL"]
    version = os.environ.get("UAU_API_VERSION", "1")
    integ = os.environ["UAU_X_INTEGRATION"]
    login = os.environ["UAU_USUARIO"]
    senha = os.environ["UAU_SENHA"]

    # 1. Autenticar → token (string) usado como header Authorization.
    token = _uau_request(base, version, "/Autenticador/AutenticarUsuario", integ,
                         payload={"Login": login, "Senha": senha})
    if isinstance(token, dict):
        token = _uau_first(token, ["token", "Token", "Authorization", "authorization"])
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Autenticação UAU não retornou um token válido")
    token = token.strip().strip('"')

    # 2. Enumerar titulares de venda (só campos válidos: empresa/obra).
    filtro = {}
    if empresa not in (None, ""):
        filtro["empresa"] = empresa
    if obra not in (None, ""):
        filtro["obra"] = obra
    titulares = _uau_as_list(_uau_request(
        base, version, "/Pessoas/ConsultarPessoasComVenda", integ,
        auth_token=token, payload=filtro))

    clients = {}
    for pessoa in titulares:
        cpf = _uau_first(pessoa, ["cpf", "Cpf", "CPF", "cpf_cnpj", "CpfCnpj",
                                  "CPFCNPJ", "CpfCnpj_pes"])
        if not cpf:
            continue
        nome = _uau_first(pessoa, ["nome", "Nome", "NomePessoa", "Nome_pes",
                                   "razaoSocial", "RazaoSocial"]) or str(cpf)
        cpf_num = re.sub(r"\D", "", str(cpf))
        # 3. Parcelas/cobranças do cliente (ValorReajustado=True → valor atualizado).
        try:
            receb = _uau_request(
                base, version, "/Recebiveis/ParcelasECobrancasDoCliente", integ,
                auth_token=token, payload={"Cpf": cpf_num, "ValorReajustado": True})
        except Exception as exc:
            print(f"[UAU] Falha ao consultar recebíveis de {cpf_num}: {exc}")
            continue
        props = _uau_parse_recebiveis(receb)
        if not props:
            continue  # sem parcelas vencidas → não é inadimplente
        clients[nome] = {
            "cpf_cnpj": str(cpf), "cel": "", "email": "",
            "endereco": "", "telefone_secundario": "", "properties": props,
        }
    return clients


def _backup_report_before_delete(cursor, rid):
    """Salva um dump JSON do relatório (clientes/imóveis/parcelas) em backups/
    antes de apagá-lo fisicamente. O arquivo é restaurável reenviando seu
    conteúdo para POST /api/reports. Retorna o caminho do backup, ou None se
    o relatório não existir."""
    row = cursor.execute(
        "SELECT report_name, COALESCE(NULLIF(report_date, ''), DATE(imported_at)) FROM reports WHERE id = ?",
        (rid,),
    ).fetchone()
    if not row:
        return None
    report_name, report_date = row
    clients = get_clients_for_report(rid)

    backup_dir = os.path.join(DIRECTORY, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-. ]', '_', report_name or "relatorio")[:60]
    backup_path = os.path.join(backup_dir, f"report_{rid}_{timestamp}_{safe_name}.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(
            {"report_name": report_name, "report_date": report_date, "clients": clients},
            f, ensure_ascii=False, indent=2,
        )
    return backup_path


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
               COALESCE(SUM(pa.valor_centavos), 0)           AS total_owed_centavos,
               COALESCE(AVG(pa.valor_centavos), 0.0)         AS avg_parcel_centavos,
               MIN(pa.vencimento_full)                       AS oldest_due,
               CAST(julianday(?) - julianday(MIN(pa.vencimento_full)) AS INTEGER) AS max_days_overdue
        FROM clients c
        LEFT JOIN properties p ON p.client_id = c.id
        LEFT JOIN parcels pa   ON pa.property_id = p.id AND pa.vencimento_full <= ?
        WHERE c.report_id = ?
          AND normalize_name(c.name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
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
            "total_owed": _cents_to_reais(r[5]),
            "avg_parcel": _cents_to_reais(r[6]),
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
    elif days <= PREJURIDICO_DAYS:
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
        WHERE normalize_name(name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
    """).fetchall()

    # Chave por identidade normalizada (K2): variações de grafia/acento do
    # mesmo cliente entre relatórios se unem numa só presença/timeline, em
    # vez de contarem como reentradas espúrias. Callers devem consultar este
    # dict com _normalize_name(name), nunca com o nome de exibição cru.
    client_presence = {}
    for rid, name in presence_rows:
        key = _normalize_name(name)
        if key not in client_presence:
            client_presence[key] = set()
        client_presence[key].add(rid)

    results = {}
    for key, rids in client_presence.items():
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

        results[key] = {
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
        report_clients[r["id"]] = {_normalize_name(row[0]) for row in rows}

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
            if _normalize_name(name) not in report_clients[subsequent_report_id]:
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
            if _normalize_name(name) not in report_clients[subsequent_report_id]:
                promises_kept += 1

    rate = round(regularized / contacted * 100, 1) if contacted > 0 else 0.0
    promises_kept_rate = round(promises_kept / promises_made * 100, 1) if promises_made > 0 else 0.0
    return {
        "contacted": contacted,
        "regularized_after_contact": regularized,
        "rate": rate,
        "promises_made": promises_made,
        "promises_kept": promises_kept,
        "promises_kept_rate": promises_kept_rate
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
    prev_cf_by_norm = {}
    if prev_report_id:
        prev_cf = _client_financials(cursor, prev_report_id, prev_report_date)
        # K2: chave também por identidade normalizada, pra "novos_pre_juridico"
        # detectar a transição mesmo se a grafia do nome mudou entre os dois
        # relatórios mais recentes (mesmo padrão de _contact_effectiveness).
        prev_cf_by_norm = {_normalize_name(n): v for n, v in prev_cf.items()}

    promessas_vencidas = []
    recontato_agendado = []
    sem_resposta = []
    novos_pre_juridico = []

    categorized_clients = set()

    for name, cf in cf_all.items():
        reentries = reentries_map.get(_normalize_name(name), {}).get("reentries", 0)
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
        if cf["max_days_overdue"] > PREJURIDICO_DAYS:
            if prev_report_id:
                prev_cf_client = prev_cf_by_norm.get(_normalize_name(name))
                if prev_cf_client and prev_cf_client["max_days_overdue"] <= PREJURIDICO_DAYS:
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


def _confirmed_paid_names(cursor):
    """K6: identidades (normalizadas, ver K2) com ao menos um desfecho
    'pagou' registrado em contact_outcomes — a qualquer momento, sem janela
    de tempo. Usado para reportar 'recuperação confirmada' (recovery_rate_
    confirmed) ao lado do sinal amplo existente (recovery_rate = 'saiu do
    relatório seguinte'), sem substituí-lo — decisão do responsável (K6,
    opção C): as duas métricas convivem, nenhuma é descartada."""
    rows = cursor.execute(
        "SELECT DISTINCT client_name FROM contact_outcomes WHERE outcome = 'pagou'"
    ).fetchall()
    return {_normalize_name(r[0]) for r in rows}


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
               COALESCE(SUM(pa.valor_centavos), 0) AS total_value_centavos
        FROM   clients   c
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  normalize_name(c.name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
        GROUP  BY c.report_id
    """).fetchall()
    all_stats_map = {r[0]: {"clients": r[1], "properties": r[2], "parcels": r[3], "total_value": _cents_to_reais(r[4])}
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

    # Busca clientes só dos relatórios em `active_report_ids` — o MESMO
    # conjunto já deduplicado usado para `reports`/`evolution` acima, em
    # ambos os caminhos (com ou sem report_ids explícito). Antes, o caminho
    # com report_ids explícito buscava direto pela lista crua (sem dedup),
    # podendo divergir do caminho default para uma seleção equivalente.
    if active_report_ids:
        placeholders = ",".join("?" for _ in active_report_ids)
        client_rows = cursor.execute(
            f"SELECT report_id, name FROM clients WHERE report_id IN ({placeholders}) "
            f"AND normalize_name(name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)",
            active_report_ids
        ).fetchall()
    else:
        client_rows = []

    # Chave por identidade normalizada (K2): "recovered" (clients_cur -
    # clients_next) precisa comparar quem é o mesmo cliente de verdade,
    # não a string exata — variação de grafia/acento entre relatórios não
    # pode aparecer como se o cliente tivesse "sumido".
    client_sets = {}
    for row in client_rows:
        client_sets.setdefault(row[0], set()).add(_normalize_name(row[1]))

    # K6: "recuperação confirmada" — subconjunto de `recovered` com um
    # desfecho 'pagou' registrado. Reportada ao lado de recovery_rate
    # (não substitui) — ver _confirmed_paid_names().
    paid_names = _confirmed_paid_names(cursor)

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
        recovered_confirmed = {n for n in recovered if n in paid_names}
        recovery_rate_confirmed = round(len(recovered_confirmed) / len(clients_cur) * 100, 1)

        transitions.append({
            "from_report":       r_cur["name"],
            "to_report":         r_next["name"],
            "total_clients":     len(clients_cur),
            "recovered_clients": len(recovered),
            "recovery_rate":     recovery_rate,
            "recovered_confirmed_clients": len(recovered_confirmed),
            "recovery_rate_confirmed":     recovery_rate_confirmed,
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
        SELECT normalize_name(c.name) AS name, MIN(rd.rdate) AS first_date
        FROM   clients c
        JOIN   report_dates rd ON rd.id = c.report_id
        GROUP  BY normalize_name(c.name)
    )
"""
# `first_seen.name` é a identidade NORMALIZADA (K2) — nunca comparar/juntar
# com c.name puro sem passar por normalize_name(c.name) do outro lado.


def get_analytics_data(start=None, end=None, report_ids=None,
                       segment="all", cutoff=None, cutoff_last_n=None):
    """
    Dados agregados para a página de Analytics: série temporal por segmento
    (novo/antigo/total), transições com taxa de recuperação por segmento e
    totais do período. Identidade de cliente usa normalize_name() (K2):
    variação de acento/caixa/espaço entre relatórios conta como o mesmo
    cliente; abreviações continuam fora do escopo.
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
               COALESCE(SUM(pa.valor_centavos), 0) AS total_value_centavos
        FROM   clients c
        JOIN   first_seen fs    ON fs.name = normalize_name(c.name)
        LEFT JOIN properties p  ON p.client_id   = c.id
        LEFT JOIN parcels    pa ON pa.property_id = p.id
        WHERE  normalize_name(c.name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
        GROUP  BY c.report_id, segment
    """, (cutoff_date,)).fetchall()

    # K7: soma em centavos inteiros (sem drift de ponto flutuante) — "total"
    # é derivado somando novo_centavos + antigo_centavos (soma de inteiros é
    # exata) e só convertido pra reais uma única vez ao final, evitando o
    # double-rounding do achado original (round(novo+antigo,2) sobre valores
    # já individualmente arredondados).
    _empty_centavos = {"clients": 0, "properties": 0, "parcels": 0, "total_value_centavos": 0}
    seg_map = {}   # report_id -> {"novo": {...}, "antigo": {...}} (valores em centavos)
    for rid, seg, n_cli, n_prop, n_parc, val_centavos in seg_rows:
        seg_map.setdefault(rid, {})[seg] = {
            "clients": n_cli, "properties": n_prop,
            "parcels": n_parc, "total_value_centavos": val_centavos,
        }

    def _finalize_segment(seg_dict):
        return {
            "clients": seg_dict["clients"], "properties": seg_dict["properties"],
            "parcels": seg_dict["parcels"],
            "total_value": _cents_to_reais(seg_dict["total_value_centavos"]),
        }

    series = []
    for r in selected:
        novo_c   = seg_map.get(r["id"], {}).get("novo",   dict(_empty_centavos))
        antigo_c = seg_map.get(r["id"], {}).get("antigo", dict(_empty_centavos))
        total = {
            "clients":     novo_c["clients"] + antigo_c["clients"],
            "properties":  novo_c["properties"] + antigo_c["properties"],
            "parcels":     novo_c["parcels"] + antigo_c["parcels"],
            "total_value": _cents_to_reais(novo_c["total_value_centavos"] + antigo_c["total_value_centavos"]),
        }
        novo, antigo = _finalize_segment(novo_c), _finalize_segment(antigo_c)
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
            SELECT c.report_id, normalize_name(c.name) AS name,
                   CASE WHEN fs.first_date >= ? THEN 'novo' ELSE 'antigo' END AS segment,
                   COALESCE(SUM(pa.valor_centavos), 0) AS value_centavos
            FROM   clients c
            JOIN   first_seen fs    ON fs.name = normalize_name(c.name)
            LEFT JOIN properties p  ON p.client_id   = c.id
            LEFT JOIN parcels    pa ON pa.property_id = p.id
            WHERE  c.report_id IN ({placeholders})
              AND  normalize_name(c.name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
            GROUP  BY c.report_id, normalize_name(c.name)
        """, [cutoff_date] + sel_ids).fetchall()

    # Chave por identidade normalizada (K2) — "recovered" entre relatórios
    # consecutivos precisa comparar o mesmo cliente de verdade, não a
    # string exata (mesma razão do client_sets em get_kpis_data).
    per_report = {}   # report_id -> {normalized_name: (segment, value_centavos)}
    for rid, name, seg, val_centavos in client_rows:
        per_report.setdefault(rid, {})[name] = (seg, val_centavos)

    def _rate(recovered, total):
        return round(len(recovered) / len(total) * 100, 1) if total else 0.0

    # K6: "recuperação confirmada" reportada ao lado de recovery_rate, não
    # em substituição a ele — ver _confirmed_paid_names().
    paid_names = _confirmed_paid_names(cursor)

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
        recovered_confirmed = {n for n in recovered if n in paid_names}

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
            "recovered_value":    _cents_to_reais(sum(cur[n][1] for n in recovered)),
            "recovered_confirmed_clients": len(recovered_confirmed),
            "recovery_rate_confirmed":     _rate(recovered_confirmed, cur_names),
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
        pre_juridico_count = len(cursor.execute(f"""
            SELECT c.name FROM clients c
            LEFT JOIN properties p ON p.client_id = c.id
            LEFT JOIN parcels pa ON pa.property_id = p.id AND pa.vencimento_full <= ?
            WHERE c.report_id = ?
              AND normalize_name(c.name) NOT IN (SELECT normalize_name(client_name) FROM kpi_exclusions)
            GROUP BY c.name
            HAVING CAST(julianday(?) - julianday(MIN(pa.vencimento_full)) AS INTEGER) > {PREJURIDICO_DAYS}
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
                "Painel local para sincronizar inadimplência do ERP UAU (API-First), "
                "gerar mensagens de cobrança via WhatsApp e acompanhar KPIs de recuperação."
            ),
            "documentation_file": "AI_CONTEXT.md",
            "entry_point": "run.py",
            "frontend": "index.html",
            "database_file": DB_FILE,
        },
        "architecture": {
            "pattern": "Servidor HTTP Python + SPA HTML/JS + SQLite local + API UAU (leitura)",
            "data_flow": [
                "POST /api/sync_uau → API UAU (Autenticar → ConsultarPessoasComVenda → "
                "ParcelasECobrancasDoCliente) → SQLite (somente leitura na UAU)",
                "WhatsApp aberto → POST /api/actions/sent → action_logs",
                "Desfecho de contato registrado → POST /api/outcomes → contact_outcomes",
                "Fallback file:// → localStorage (sem servidor)",
            ],
        },
        "database_schema": {
            "reports": "Relatórios históricos importados (report_name, report_date)",
            "clients": "Clientes inadimplentes por relatório (name, cpf_cnpj, cel, email)",
            "properties": "Imóveis do cliente (venda_id, identifier)",
            "parcels": "Parcelas em atraso (parcela, vencimento, vencimento_full, valor R$)",
            "action_logs": "Histórico de disparos WhatsApp (venda_id, client_name, sent_at)",
            "kpi_exclusions": "Clientes excluídos manualmente dos cálculos de KPI",
            "contact_outcomes": "Registros de desfechos de contatos (client_name, outcome, promised_date, next_contact, note)",
            "access_audit": "Auditoria (S6): quem consultou o perfil/PII de qual cliente e quando (operator, client_name, accessed_at)",
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
            "POST /api/sync_uau": "Sincroniza inadimplência da API UAU (somente leitura) {empresa?, obra?}",
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
            "GET /api/audit": "Trilha de auditoria de acesso a PII individual (?name&limit) — quem viu o perfil de qual cliente e quando",
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
                "Taxa = clientes em R_n que NÃO aparecem em R_{n+1} / total em R_n × 100 "
                "('saiu do relatório' — sinal amplo, não implica pagamento confirmado). "
                "recovery_rate_confirmed (K6) reporta, ao lado, a fração desses que têm "
                "um desfecho 'pagou' registrado em contact_outcomes — as duas métricas "
                "convivem, nenhuma substitui a outra."
            ),
            "client_segmentation": (
                "Cliente é 'novo' se sua primeira aparição em qualquer relatório "
                "(identidade via normalize_name(), K2 — acento/caixa/espaço não distinguem "
                "clientes) ocorreu na data de corte ou depois; senão 'antigo'. "
                "Corte configurável por data (cutoff) ou N últimos relatórios (cutoff_last_n)."
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
            "offline_fallback": (
                "Se aberto via file://, dados vão para localStorage "
                "(inad_clients_db, inad_sent, inad_kpi_exclusions)."
            ),
            "privacy": (
                "Nunca commitar .db, .json com dados reais ou credenciais (.env) — ver .gitignore."
            ),
            "access_audit": (
                "S6: GET /api/clients/profile registra em access_audit (operator, "
                "client_name, accessed_at) — ver _log_access(). GET /api/reports/<id> "
                "(leitura em lote do relatório inteiro) não é logado, para não afogar "
                "a trilha com o uso rotineiro do painel. Criptografia at-rest do "
                "banco (SQLCipher) foi avaliada e não implementada — decisão do "
                "responsável de confiar na criptografia de disco do SO (FileVault/"
                "BitLocker) em vez disso."
            ),
            "frontend_edit_rule": (
                "Frontend é um único arquivo: editar index.html diretamente."
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
        },
        "ai_guidelines": [
            "Leia AI_CONTEXT.md antes de alterações significativas.",
            "Frontend é um único arquivo — edite index.html diretamente.",
            "Integração UAU é SOMENTE LEITURA — nunca usar endpoints de escrita do ERP.",
            "Use sqlite3 nativo — sem ORMs ou drivers externos de banco.",
            "Preserve fallback localStorage para protocolo file://.",
            "Mantenha endpoints REST retrocompatíveis (/api/sent ↔ /api/actions/sent).",
        ],
        "markdown": ai_context_md,
    }


# ─── HANDLER HTTP ─────────────────────────────────────────────────────────────

def _json_response(handler, data, status=200):
    """Envia resposta JSON. Sem CORS: o frontend é servido pelo mesmo
    servidor (same-origin), então requisições cross-origin não precisam
    funcionar — e não devem (evita que outro site consulte/altere dados)."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type",   "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.end_headers()
    handler.wfile.write(body)


def _error_response(handler, exc, status=500):
    """Loga o erro completo (com traceback) em inad_errors.log e responde ao
    cliente com uma mensagem genérica — nunca vaza str(exc)/detalhes internos
    na resposta HTTP."""
    import traceback
    logging.error(
        f"Erro em {handler.command} {handler.path}: {exc}\n{traceback.format_exc()}"
    )
    message = "Requisição inválida" if status == 400 else "Erro interno"
    _json_response(handler, {"error": message}, status)


_BODY_TOO_LARGE = object()  # sentinel: Content-Length excede MAX_BODY_BYTES


def _read_body(handler):
    """Lê o corpo do POST de forma segura.
    Retorna None se Content-Length ausente/inválido, _BODY_TOO_LARGE se exceder
    MAX_BODY_BYTES (sem tentar ler o corpo), ou os bytes do corpo."""
    length = handler.headers.get("Content-Length")
    if length is None:
        return None
    try:
        length = int(length)
    except ValueError:
        return None
    if length < 0:
        return None
    if length > MAX_BODY_BYTES:
        return _BODY_TOO_LARGE
    try:
        return handler.rfile.read(length)
    except OSError:
        return None


# Único endpoint acessível sem token mesmo com o servidor exposto na rede
# (monitoramento/health-check).
_PUBLIC_PATHS = {"/api/health"}


def _request_token(handler):
    """Token do operador: header X-INAD-Token, ou query string ?token=
    (necessário para o carregamento inicial da página, que o navegador faz
    sem headers customizados — o bootstrap no HTML lê ?token= uma vez e passa
    a mandar o header em todas as chamadas fetch() seguintes)."""
    token = handler.headers.get("X-INAD-Token", "")
    if token:
        return token
    if "?" in handler.path:
        from urllib.parse import parse_qs
        params = parse_qs(handler.path.split("?", 1)[1])
        return params.get("token", [""])[0]
    return ""


def _authenticate(handler):
    """Retorna (autorizado: bool, operador: str|None, pode_escrever: bool).
    Em bind loopback (padrão local), sempre autorizado com escrita — o dono da
    máquina tem acesso total; autenticação/papel só são exigidos quando o
    servidor está exposto além de localhost (ver _is_loopback_bind() e a
    checagem de boot em __main__). Operador com can_write=0 é autenticado
    normalmente (pode GET) mas volta pode_escrever=False — do_POST/do_DELETE
    usam esse sinal para responder 403."""
    if _is_loopback_bind():
        return True, "local", True
    path = handler.path.split("?")[0]
    if path in _PUBLIC_PATHS:
        return True, None, True
    token = _request_token(handler)
    if not token:
        return False, None, False
    token_hash = _hash_token(token)
    rows = get_conn().cursor().execute(
        "SELECT name, token_hash, can_write FROM operators WHERE active = 1"
    ).fetchall()
    for name, stored_hash, can_write in rows:
        if hmac.compare_digest(token_hash, stored_hash):
            return True, name, bool(can_write)
    return False, None, False


def _log_access(conn, operator, client_name):
    """S6: registra em access_audit quem consultou o perfil (CPF/telefone/
    endereço) de qual cliente e quando. Chamado só em leituras que expõem
    PII de UM cliente específico (GET /api/clients/profile) — não em
    listagens em lote (GET /api/reports/<id>), pra não afogar a trilha com
    o uso normal e rotineiro do painel (toda tela renderiza a partir do
    relatório inteiro; isso não é uma "consulta" no sentido de auditoria)."""
    conn.execute(
        "INSERT INTO access_audit (operator, client_name) VALUES (?, ?)",
        (operator, client_name),
    )
    conn.commit()


# Únicos arquivos estáticos servidos pelo fallback do SimpleHTTPRequestHandler
# — qualquer outro caminho (incluindo run.py, *.db, .git/*, scripts/*, etc.,
# que de outra forma o SimpleHTTPRequestHandler serviria sem restrição) recebe 404.
_STATIC_ALLOWLIST = {
    "/index.html", "/inad_analytics.html",
    "/analytics.css", "/analytics.js",
    "/libs/chart.umd.min.js",
}


class INADHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, fmt, *args):
        pass   # Silencia logs de acesso HTTP

    def do_OPTIONS(self):
        # Sem suporte a CORS cross-origin (ver _json_response) — respondemos
        # 204 só para não quebrar clientes que mandem um preflight à toa.
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _redirect(self, location):
        # Preserva ?token= no destino, senão o bootstrap da página seguinte
        # nunca recebe o token e o operador cai em 401. O token precisa entrar
        # ANTES de um eventual #fragmento (ex.: /index.html#kpi) —
        # tudo depois de "#" é fragmento, não query string.
        token = _request_token(self)
        if token and "token=" not in location:
            base, _, frag = location.partition("#")
            sep = "&" if "?" in base else "?"
            location = f"{base}{sep}token={token}" + (f"#{frag}" if frag else "")
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
            ok, operator, _can_write = _authenticate(self)
            if not ok:
                _json_response(self, {"error": "Não autorizado"}, 401)
                return
            self.operator_name = operator
            self._do_GET_unwrapped()
        except Exception as exc:
            try:
                _error_response(self, exc, 500)
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
            self._redirect("/index.html")
        elif path in ("/kpi", "/kpis"):
            self._redirect("/index.html#kpi")
        elif path in ("/cobranca", "/painel"):
            self._redirect("/index.html#cobranca")
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
                params = parse_qs(self.path.split("?", 1)[1])
                ids_str = params.get("reports", [""])[0]
                if ids_str:
                    try:
                        report_ids = [int(x) for x in ids_str.split(",")]
                    except ValueError:
                        _json_response(self, {"error": "Parâmetro 'reports' inválido: todos os ids devem ser inteiros"}, 400)
                        return
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
                    _json_response(self, {"error": "Parâmetro 'reports' inválido: todos os ids devem ser inteiros"}, 400)
                    return

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
                _error_response(self, exc, 500)

        elif path == "/api/kpis/exclusions":
            cursor = get_conn().cursor()
            rows = cursor.execute("SELECT client_name FROM kpi_exclusions").fetchall()
            _json_response(self, [r[0] for r in rows])

        elif path == "/api/health":
            _json_response(self, {"status": "ok", "port": PORT,
                                   "platform": platform.system(),
                                   "python": platform.python_version(),
                                   "db_file": DB_FILE})

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
            limit_val = min(limit_val, MAX_RESULT_LIMIT)

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
                reentries = reentries_map.get(_normalize_name(name), {}).get("reentries", 0)
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

            conn = get_conn()
            cursor = conn.cursor()
            exists = cursor.execute("SELECT 1 FROM clients WHERE name = ? LIMIT 1", (name,)).fetchone()
            if not exists:
                _json_response(self, {"error": f"Cliente '{name}' nao encontrado no sistema"}, 404)
                return

            _log_access(conn, self.operator_name, name)

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
                "121+": {"parcels": 0, "value": 0.0}
            }
            # K7: acumula em centavos inteiros (soma exata, sem drift de
            # round() repetido a cada parcela) — converte pra reais só ao final.
            bucket_cents = {k: 0 for k in buckets_data}
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
                        SELECT parcela, vencimento, vencimento_full, valor, valor_centavos FROM parcels
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
                            bucket_cents[b] += pa[4]
                        except Exception:
                            pass
                    properties_list.append({
                        "venda_id": p_vid, "identifier": p_ident, "parcels": parcels_list
                    })
                for b in buckets_data:
                    buckets_data[b]["value"] = _cents_to_reais(bucket_cents[b])

            reentries_map = _calculate_reentries(cursor)
            rec_info = reentries_map.get(_normalize_name(name), {
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
            # Clamp nos dois extremos: limit_val vai direto para "LIMIT ?" no SQL,
            # onde SQLite trata valores negativos como "sem limite".
            limit_val = max(1, min(limit_val, MAX_RESULT_LIMIT))
                    
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

        elif path == "/api/audit":
            # S6: trilha de auditoria — quem consultou o perfil (CPF/telefone/
            # endereço) de qual cliente e quando (ver _log_access()).
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
            limit_val = max(1, min(limit_val, MAX_RESULT_LIMIT))

            cursor = get_conn().cursor()
            if cname:
                rows = cursor.execute("""
                    SELECT id, operator, client_name, accessed_at
                    FROM access_audit
                    WHERE client_name = ?
                    ORDER BY accessed_at DESC
                    LIMIT ?
                """, (cname, limit_val)).fetchall()
            else:
                rows = cursor.execute("""
                    SELECT id, operator, client_name, accessed_at
                    FROM access_audit
                    ORDER BY accessed_at DESC
                    LIMIT ?
                """, (limit_val,)).fetchall()

            res = [{
                "id": r[0], "operator": r[1], "client_name": r[2], "accessed_at": r[3]
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
                    "effectiveness": {"contacted": 0, "regularized_after_contact": 0, "rate": 0.0, "promises_made": 0, "promises_kept": 0, "promises_kept_rate": 0.0},
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
                "121+": {"clients": 0, "value": 0.0}
            }
            pre_juridico_count = 0
            pre_juridico_value = 0.0
            for cf in cf_all.values():
                b = _bucketize(cf["max_days_overdue"])
                aging_distribution[b]["clients"] += 1
                aging_distribution[b]["value"] = round(aging_distribution[b]["value"] + cf["total_owed"], 2)
                if cf["max_days_overdue"] > PREJURIDICO_DAYS:
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
            top_val = max(1, min(top_val, MAX_RESULT_LIMIT))
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

        elif path in _STATIC_ALLOWLIST:
            super().do_GET()

        else:
            # Nunca serve arquivos fora do allowlist acima — sem isso, o
            # SimpleHTTPRequestHandler serviria QUALQUER arquivo do diretório
            # do projeto (run.py, o .db inteiro, scripts/, .git/, etc.) a
            # quem pedir pelo nome.
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        """
        Ponto de entrada para requisições POST.
        Implementa um wrapper global de tratamento de erros que registra falhas críticas no log persistente.
        """
        try:
            ok, operator, can_write = _authenticate(self)
            if not ok:
                _json_response(self, {"error": "Não autorizado"}, 401)
                return
            if not can_write:
                _json_response(self, {"error": "Operador somente-leitura: edição não permitida"}, 403)
                return
            self.operator_name = operator
            self._do_POST_unwrapped()
        except Exception as exc:
            try:
                _error_response(self, exc, 500)
            except Exception:
                pass

    def _do_POST_unwrapped(self):
        """
        Executa as ações de alteração e inserção de dados do painel,
        como gravação de relatórios de cobrança e cadastro de desfechos de contato.
        """
        path = self.path.split("?")[0]
        body = _read_body(self)
        if body is _BODY_TOO_LARGE:
            _json_response(self, {"error": "Corpo da requisição excede o limite permitido"}, 413)
            return
        if body is None:
            _json_response(self, {"error": "Content-Length ausente"}, 400)
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _error_response(self, exc, 400)
            return

        if path in ("/api/reports", "/api/clients"):
            report_name = payload.get(
                "report_name", f"Relatório {time.strftime('%d/%m/%Y %H:%M')}"
            )
            report_date = payload.get("report_date")
            if not report_date:
                _json_response(self, {
                    "error": "report_date é obrigatório (data de emissão do relatório, "
                             "extraída do PDF) — não é permitido importar sem ela"
                }, 400)
                return
            clients     = payload.get("clients") or (
                payload if "report_name" not in payload else {}
            )
            conn   = get_conn()
            try:
                report_date = _normalize_date(report_date)
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
            except ValueError as exc:
                conn.rollback()
                _json_response(self, {"error": str(exc)}, 400)
            except Exception as exc:
                conn.rollback()
                _error_response(self, exc, 500)

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
                _error_response(self, exc, 500)

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
                _error_response(self, exc, 500)

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
                _error_response(self, exc, 500)

        elif path == "/api/sync_uau":
            conn = None
            try:
                if not all([os.environ.get("UAU_BASE_URL"),
                            os.environ.get("UAU_USUARIO"),
                            os.environ.get("UAU_SENHA"),
                            os.environ.get("UAU_X_INTEGRATION")]):
                    _json_response(self, {"error": "Credenciais da UAU ausentes no arquivo .env"}, 500)
                    return

                # Filtro opcional empresa/obra vindo do corpo do POST (limita o volume).
                empresa = payload.get("empresa") if isinstance(payload, dict) else None
                obra    = payload.get("obra") if isinstance(payload, dict) else None

                # Consulta real à UAU (somente leitura). Retorna só inadimplentes.
                clients = _sync_from_uau(empresa, obra)
                if not clients:
                    _json_response(self, {
                        "status": "empty",
                        "message": "Nenhum cliente inadimplente retornado pela UAU "
                                   "para o filtro informado."})
                    return

                conn   = get_conn()
                cursor = conn.cursor()
                report_name = f"UAU Sync {time.strftime('%d/%m/%Y %H:%M')}"
                report_date = datetime.date.today().isoformat()
                cursor.execute(
                    "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
                    (report_name, report_date),
                )
                report_id = cursor.lastrowid
                _insert_clients(cursor, report_id, clients)
                conn.commit()
                print(f"[API] Sincronização UAU concluída. Relatório ID: {report_id} "
                      f"| {len(clients)} cliente(s) inadimplente(s)")
                _json_response(self, {"status": "success", "report_id": report_id,
                                      "clients": len(clients)})
            except urllib.error.HTTPError as exc:
                if conn is not None:
                    conn.rollback()
                _error_response(self, RuntimeError(f"UAU respondeu HTTP {exc.code}: {exc.reason}"), 502)
            except urllib.error.URLError as exc:
                if conn is not None:
                    conn.rollback()
                _error_response(self, RuntimeError(f"Falha ao conectar na API UAU: {exc.reason}"), 502)
            except Exception as exc:
                if conn is not None:
                    conn.rollback()
                _error_response(self, exc, 500)

        else:
            _json_response(self, {"error": "Rota não encontrada"}, 404)

    # ── DELETE ────────────────────────────────────────────────────────────────
    def do_DELETE(self):
        """
        Ponto de entrada para requisições DELETE.
        Exclui relatórios ou desfechos de contato, registrando qualquer falha no log persistente.
        """
        try:
            ok, operator, can_write = _authenticate(self)
            if not ok:
                _json_response(self, {"error": "Não autorizado"}, 401)
                return
            if not can_write:
                _json_response(self, {"error": "Operador somente-leitura: edição não permitida"}, 403)
                return
            self.operator_name = operator
            self._do_DELETE_unwrapped()
        except Exception as exc:
            try:
                _error_response(self, exc, 500)
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

                # Backup recuperável antes da exclusão irreversível (dados sensíveis — nunca versionar)
                backup_path = _backup_report_before_delete(cursor, rid)

                # A exclusão cascateará devido a restrição ON DELETE CASCADE nos relacionamentos
                cursor.execute("DELETE FROM reports WHERE id = ?", (rid,))
                conn.commit()
                _json_response(self, {
                    "status": "success",
                    "backup": os.path.basename(backup_path) if backup_path else None,
                })
            except (ValueError, IndexError):
                _json_response(self, {"error": "ID inválido"}, 400)
            except Exception as exc:
                _error_response(self, exc, 500)
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
                _error_response(self, exc, 500)
        else:
            _json_response(self, {"error": "Rota não encontrada"}, 404)


# ─── SERVIDOR ─────────────────────────────────────────────────────────────────

class _ReuseServer(socketserver.TCPServer):
    """TCPServer com reutilização de porta compatível com Windows e UNIX.

    Deliberadamente NÃO herda socketserver.ThreadingMixIn: o servidor atende
    uma requisição HTTP por vez (modelo simples, adequado a um CRM local de
    poucos operadores). O `threading.local`/`check_same_thread=False` na
    conexão SQLite (get_conn(), acima) existe só porque a thread principal de
    serve_forever() é distinta da thread de import/setup — não porque
    múltiplas requisições HTTP rodem concorrentemente. Se algum dia migrar
    para ThreadingHTTPServer, revisar todo cursor/conexão compartilhado antes
    (acesso a SQLite não é thread-safe por padrão)."""
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
        _httpd = _ReuseServer((HOST, PORT), INADHandler)
        _httpd.serve_forever()
    except OSError as exc:
        print(f"\n[ERRO] Não foi possível iniciar o servidor na porta {PORT}: {exc}")
        print(f"       Tente usar outra porta: INAD_PORT=9090 python3 run.py")
        sys.exit(1)


# ─── PONTO DE ENTRADA ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Comandos de gestão de operadores (rodam e saem — não sobem o servidor).
    if "--add-operator" in sys.argv:
        _idx = sys.argv.index("--add-operator")
        _name = sys.argv[_idx + 1] if _idx + 1 < len(sys.argv) else None
        if not _name or _name.startswith("--"):
            print('Uso: python run.py --add-operator "Nome do operador" [--read-only]')
            sys.exit(1)
        # --read-only: operador só pode GET; POST/DELETE respondem 403.
        _can_write = "--read-only" not in sys.argv
        init_db()
        _token = _add_operator(_name, can_write=_can_write)
        _role = "leitura+escrita" if _can_write else "SOMENTE-LEITURA"
        print(f"Operador '{_name}' criado ({_role}).")
        print("Token (guarde em local seguro — não será exibido de novo):")
        print(f"  {_token}")
        print(f"\nUso pelo operador: header 'X-INAD-Token: {_token}' ou ?token={_token} na URL.")
        sys.exit(0)

    if "--list-operators" in sys.argv:
        init_db()
        _ops = _list_operators()
        if not _ops:
            print("Nenhum operador cadastrado.")
        else:
            for _op in _ops:
                _status = "ativo" if _op["active"] else "revogado"
                _role = "escrita" if _op["can_write"] else "somente-leitura"
                print(f"  {_op['name']:<30} {_status:<10} {_role:<16} criado em {_op['created_at']}")
        sys.exit(0)

    if "--revoke-operator" in sys.argv:
        _idx = sys.argv.index("--revoke-operator")
        _name = sys.argv[_idx + 1] if _idx + 1 < len(sys.argv) else None
        if not _name:
            print('Uso: python run.py --revoke-operator "Nome do operador"')
            sys.exit(1)
        init_db()
        _ok = _revoke_operator(_name)
        print(f"Operador '{_name}' {'revogado' if _ok else 'não encontrado'}.")
        sys.exit(0 if _ok else 1)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    try:
        signal.signal(signal.SIGINT, _shutdown_handler)
    except OSError:
        pass  # Windows não suporta SIGINT via signal.signal em todos os contextos

    # Bind além de localhost exige pelo menos um operador cadastrado — sem
    # isso, qualquer um na rede acessaria os dados sem nenhuma autenticação.
    if not _is_loopback_bind():
        init_db()
        if not _has_active_operators():
            print(f"\n[ERRO] INAD_HOST={HOST!r} expõe o servidor além de localhost, mas")
            print("       nenhum operador está cadastrado. Cadastre um antes de continuar:")
            print('         python run.py --add-operator "Seu Nome"')
            sys.exit(1)

    print("══════════════════════════════════════════════════")
    print("  INAD · Painel de Cobrança")
    print(f"  Plataforma : {platform.system()} {platform.machine()}")
    print(f"  Python     : {platform.python_version()}")
    print(f"  Porta      : {PORT}  (use INAD_PORT=XXXX para mudar)")
    print(f"  Endereço   : {HOST}  (use INAD_HOST/--host para expor na rede)")
    print(f"  Modo       : {'Servidor headless' if HEADLESS else 'Local (abre navegador)'}")
    if not _is_loopback_bind():
        print(f"  ⚠ REDE     : exposto além de localhost — autenticação por token exigida")
    print("══════════════════════════════════════════════════")

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(0.8)

    url = f"http://localhost:{PORT}/index.html"
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
