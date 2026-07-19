"""
INAD — Gerador de Dados de Demonstração
Popula o banco DEMO (inad_demo.db) com dados fictícios realistas para testar
KPIs, segmentação novo/antigo e a página de Analytics sem tocar no banco real.

Uso:
  python3 generate_demo_data.py           → Gera dados se o banco demo estiver vazio
  python3 generate_demo_data.py --reset   → Apaga e recria o banco demo do zero
  python3 generate_demo_data.py --seed 7  → Usa outra seed (padrão: 42, determinística)
"""

import os
import sys
import random
import calendar

# Trava o modo demo ANTES de importar run.py — garante que run.DB_PATH
# aponte para inad_demo.db e este script jamais toque no banco real.
os.environ["INAD_DEMO"] = "1"

import run  # noqa: E402

assert run.DEMO, "Falha de segurança: run.py não entrou em modo demo."
assert run.DB_FILE == "inad_demo.db", f"Banco inesperado: {run.DB_FILE}"

# ─── NOMES FICTÍCIOS ──────────────────────────────────────────────────────────
FIRST_NAMES = [
    "ANA", "BRUNO", "CARLA", "DIEGO", "ELAINE", "FABIO", "GABRIELA", "HENRIQUE",
    "INGRID", "JOAO", "KARINA", "LUCAS", "MARIANA", "NELSON", "OTAVIO", "PATRICIA",
    "RAFAEL", "SILVIA", "THIAGO", "VANESSA", "WAGNER", "XENIA", "YARA", "ZECA",
    "ADRIANA", "BERNARDO", "CintiA", "DANILO", "EDUARDA", "FERNANDO",
]
LAST_NAMES = [
    "SILVA", "SANTOS", "OLIVEIRA", "SOUZA", "PEREIRA", "COSTA", "RODRIGUES",
    "ALMEIDA", "NASCIMENTO", "LIMA", "ARAUJO", "FERREIRA", "RIBEIRO", "CARVALHO",
    "GOMES", "MARTINS", "ROCHA", "BARBOSA", "MOREIRA", "CAMPOS",
]

QUADRAS = ["QD 01", "QD 02", "QD 05", "QD 07", "QD 10", "QD 12", "QD 15"]


def _make_name(rng):
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}".upper()


def _make_cpf(rng):
    return f"{rng.randint(100,999)}.{rng.randint(100,999)}.{rng.randint(100,999)}-{rng.randint(10,99)}"


def _make_cel(rng):
    return f"(62) 9{rng.randint(6000,9999)}-{rng.randint(1000,9999)}"


def _make_client(rng, name, force_vencimento=None):
    """Monta um cliente no formato exato que run._insert_clients espera."""
    first = name.split()[0].lower()
    props = []
    for _ in range(rng.choices([1, 2, 3], weights=[70, 22, 8])[0]):
        parcels = []
        n_parcels = rng.randint(1, 6)
        if force_vencimento:
            y, m, d = map(int, force_vencimento.split('-'))
            parcels.append({
                "parcela": "001/240",
                "vencimento": f"{d:02d}/{m:02d}",
                "vencimento_full": force_vencimento,
                "valor": round(rng.uniform(1000.0, 3000.0), 2),
            })
            for i in range(1, n_parcels):
                nm = (m + i - 1) % 12 + 1
                ny = y + (m + i - 1) // 12
                nd = min(rng.randint(1, 28), calendar.monthrange(ny, nm)[1])
                parcels.append({
                    "parcela": f"{i+1:03d}/240",
                    "vencimento": f"{nd:02d}/{nm:02d}",
                    "vencimento_full": f"{ny}-{nm:02d}-{nd:02d}",
                    "valor": round(rng.uniform(300.0, 2000.0), 2),
                })
        else:
            base_year = rng.randint(2023, 2025)
            base_month = rng.randint(1, 12)
            for i in range(n_parcels):
                month = (base_month + i - 1) % 12 + 1
                year = base_year + (base_month + i - 1) // 12
                day = min(rng.randint(1, 28), calendar.monthrange(year, month)[1])
                parcels.append({
                    "parcela": f"{rng.randint(1, 240):03d}/240",
                    "vencimento": f"{day:02d}/{month:02d}",
                    "vencimento_full": f"{year}-{month:02d}-{day:02d}",
                    "valor": round(rng.uniform(300.0, 5000.0), 2),
                })
        props.append({
            "venda_id": str(rng.randint(10000, 99999)),
            "identifier": f"{rng.choice(QUADRAS)} LT {rng.randint(1, 40):02d}",
            "parcels": parcels,
        })
    return {
        "cpf_cnpj": _make_cpf(rng),
        "cel": _make_cel(rng),
        "email": f"{first}.demo@example.com",
        "properties": props,
    }


def generate(seed=42):
    rng = random.Random(seed)
    conn = run.get_conn()
    cursor = conn.cursor()

    if cursor.execute("SELECT COUNT(*) FROM reports").fetchone()[0] > 0:
        print("Banco demo já contém dados. Use --reset para recriar do zero.")
        return

    # Pool de nomes únicos + variações propositais de grafia (exercitam a
    # limitação conhecida de identidade por nome exato)
    pool = set()
    while len(pool) < 80:
        pool.add(_make_name(rng))
    pool = sorted(pool)
    variant_sources = rng.sample(pool, 3)
    variants = [n.replace("A", "Á", 1) for n in variant_sources]

    # 15 relatórios mensais: churn de ~10-20% de recuperados + novos entrantes
    months = []
    for year in (2025, 2026):
        for month in range(1, 13):
            if year == 2026 and month > 3:
                break
            months.append((year, month))

    available = list(pool)
    rng.shuffle(available)
    active = available[:45]          # carteira inicial de inadimplentes
    waiting = available[45:]         # entram ao longo do tempo

    pre_jur_targets = []
    reports_info = []

    total_reports = 0
    for idx, (year, month) in enumerate(months):
        last_day = calendar.monthrange(year, month)[1]
        rdate = f"{year}-{month:02d}-{last_day:02d}"

        # Recupera (remove) 10-20% e adiciona 2-6 novos
        n_recovered = max(1, int(len(active) * rng.uniform(0.10, 0.20)))
        to_remove = rng.sample(active, n_recovered)
        for name in to_remove:
            active.remove(name)
            
        if reports_info:
            reports_info[-1]["to_remove_next"] = to_remove

        entrants = [waiting.pop() for _ in range(min(rng.randint(2, 6), len(waiting)))]
        active.extend(entrants)

        # Nos últimos relatórios, injeta as variações de grafia
        if idx >= len(months) - 3 and variants:
            active.append(variants.pop())

        # Identifica alvos persistentes para cruzar 120 dias no penúltimo ciclo
        if idx == len(months) - 2:
            pre_jur_targets = sorted(list(active))[:3]

        clients = {}
        for name in sorted(active):
            force_v = None
            if name in pre_jur_targets:
                force_v = "2025-11-10"
            elif idx == len(months) - 1 and name in entrants[:5]:
                # Clientes com dívidas recentes (0-30 dias) na última rodada
                force_v = "2026-03-15"
            clients[name] = _make_client(rng, name, force_vencimento=force_v)

        cursor.execute(
            "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
            (f"Relatório Demo {month:02d}/{year}", rdate),
        )
        report_id = cursor.lastrowid
        run._insert_clients(cursor, report_id, clients)
        
        reports_info.append({
            "report_id": report_id,
            "report_date": rdate,
            "clients": list(clients.keys()),
            "to_remove_next": []
        })
        total_reports += 1

    _simulate_contacts(rng, cursor, reports_info)

    conn.commit()
    n_clients = cursor.execute("SELECT COUNT(DISTINCT name) FROM clients").fetchone()[0]
    n_parcels = cursor.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    total_val = cursor.execute("SELECT ROUND(SUM(valor), 2) FROM parcels").fetchone()[0]
    print(f"Banco demo populado: {total_reports} relatórios, "
          f"{n_clients} clientes únicos, {n_parcels} parcelas, R$ {total_val:,.2f} total.")


def _simulate_contacts(rng, cursor, reports_info):
    """
    Simula de forma realista contatos e desfechos, correlacionados com a taxa de regularização.
    """
    import datetime
    total_contacts = 0
    total_outcomes = {ot: 0 for ot in run.OUTCOME_TYPES}

    for idx, rep in enumerate(reports_info):
        report_id = rep["report_id"]
        report_date = rep["report_date"]
        clients_list = rep["clients"]
        to_remove_next = rep.get("to_remove_next", [])

        y, m, d = map(int, report_date.split("-"))
        rep_dt = datetime.date(y, m, d)

        # Contata ~50% a 70% dos clientes ativos
        n_contact = int(len(clients_list) * rng.uniform(0.5, 0.7))
        contacted_clients = rng.sample(clients_list, n_contact)

        for name in contacted_clients:
            cursor.execute("""
                SELECT p.venda_id FROM properties p
                JOIN clients c ON p.client_id = c.id
                WHERE c.report_id = ? AND c.name = ?
                LIMIT 1
            """, (report_id, name))
            row = cursor.fetchone()
            venda_id = row[0] if row else "0000"

            # Data de contato: report_date + rng(1,10) dias
            contact_days = rng.randint(1, 10)
            contact_dt = rep_dt + datetime.timedelta(days=contact_days)
            contact_date_str = contact_dt.strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                INSERT INTO action_logs (venda_id, client_name, sent_at)
                VALUES (?, ?, ?)
            """, (venda_id, name, contact_date_str))
            action_log_id = cursor.lastrowid
            total_contacts += 1

            # Desfecho em ~65% dos contatos
            if rng.random() < 0.65:
                is_regularizing = name in to_remove_next
                if is_regularizing:
                    outcome = rng.choices(
                        ["prometeu_pagar", "pagou", "negociacao", "sem_resposta", "outro"],
                        weights=[50, 25, 15, 5, 5]
                    )[0]
                else:
                    outcome = rng.choices(
                        ["sem_resposta", "prometeu_pagar", "negociacao", "recusou", "numero_invalido", "outro"],
                        weights=[45, 20, 15, 10, 8, 2]
                    )[0]

                promised_date_str = None
                next_contact_str = None

                if outcome == "prometeu_pagar":
                    promised_dt = contact_dt + datetime.timedelta(days=rng.randint(5, 20))
                    promised_date_str = promised_dt.strftime("%Y-%m-%d")
                elif outcome in ("sem_resposta", "negociacao"):
                    next_dt = contact_dt + datetime.timedelta(days=rng.randint(7, 15))
                    next_contact_str = next_dt.strftime("%Y-%m-%d")

                cursor.execute("""
                    INSERT INTO contact_outcomes (client_name, venda_id, action_log_id, outcome, promised_date, next_contact, note, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, venda_id, action_log_id, outcome, promised_date_str, next_contact_str, f"Simulacao de contato demo ({outcome})", contact_date_str))
                total_outcomes[outcome] += 1

    print(f"Contatos simulados: {total_contacts} disparos logados.")
    print("Desfechos simulados: " + ", ".join(f"{k}: {v}" for k, v in total_outcomes.items()))


if __name__ == "__main__":
    seed = 42
    if "--seed" in sys.argv:
        try:
            seed = int(sys.argv[sys.argv.index("--seed") + 1])
        except (IndexError, ValueError):
            print("Seed inválida; usando 42.")

    if "--reset" in sys.argv:
        for suffix in ("", "-shm", "-wal"):
            path = run.DB_PATH + suffix
            if os.path.exists(path):
                os.remove(path)
        print(f"Banco demo removido: {run.DB_FILE}")

    print(f"Alvo: {run.DB_PATH}")
    run.init_db()
    generate(seed)
