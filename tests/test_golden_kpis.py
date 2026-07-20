"""
Testes golden de corretude de KPI + reconciliação (Fase 0-B, pré-requisito).

Roda inteiramente sobre bancos SQLite temporários — nunca toca em
inad_database.db. Não depende de nenhum gerador de dados externo: os datasets
(pequeno calculado à mão, e maior sintético) são gerados aqui mesmo.

Uso:
  python -m unittest discover -s tests -v
  python tests/test_golden_kpis.py
"""

import os
import sys
import random
import shutil
import tempfile
import unittest

os.environ.setdefault("INAD_HEADLESS", "1")

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import run  # noqa: E402


def _fresh_db(tmp_dir, name="test.db"):
    """Redireciona run.DB_PATH para um arquivo temporário isolado e cria o schema."""
    if hasattr(run._local, "conn") and run._local.conn is not None:
        run._local.conn.close()
        run._local.conn = None
    run.DB_PATH = os.path.join(tmp_dir, name)
    run.init_db()


def _import_report(name, date, clients, escopo="completo", escopo_motivo="", run_heuristic=False):
    """Insere um relatório + árvore de clientes usando o caminho real de
    ingestão de run.py (_normalize_date/_insert_clients), igual à API.

    REFORMA_KPI: por padrão marca escopo='completo' — as fixtures deste
    arquivo são datasets pequenos, mas deliberadamente completos (calculados
    à mão pelo autor do teste, não um recorte real de PDF); é o mesmo que um
    operador confiante declarando escopo no import. `run_heuristic=True` é só
    para os testes que verificam a própria heurística de detecção (ela pode
    rebaixar o escopo declarado — não usar nos demais testes, que dependem
    de evolution/transitions não-vazios)."""
    conn = run.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reports (report_name, report_date, escopo, escopo_motivo, escopo_origem) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, run._normalize_date(date), escopo, escopo_motivo,
         "declarado_usuario" if escopo else ""),
    )
    report_id = cursor.lastrowid
    run._insert_clients(cursor, report_id, clients)
    conn.commit()
    if run_heuristic:
        run._classificar_escopo_heuristico(cursor, conn, report_id)
    return report_id


def _generate_synthetic_reports(rng, n_reports=10, pool_size=30):
    """Gera uma série determinística de relatórios mensais com churn de
    clientes (alguns saem, alguns entram a cada mês), só para ter volume
    suficiente e exercitar reconciliação — não tenta replicar todas as
    nuances de um gerador de dados de demonstração, só produzir uma série
    plausível e determinística (mesma seed => mesmo resultado sempre)."""
    first_names = ["ANA", "BRUNO", "CARLA", "DIEGO", "ELAINE", "FABIO", "GABRIELA",
                    "HENRIQUE", "INGRID", "JOAO", "KARINA", "LUCAS", "MARIANA", "NELSON",
                    "OTAVIO", "PATRICIA", "RAFAEL", "SILVIA", "THIAGO", "VANESSA"]
    last_names = ["SILVA", "SANTOS", "OLIVEIRA", "SOUZA", "PEREIRA", "COSTA",
                  "RODRIGUES", "ALMEIDA", "NASCIMENTO", "LIMA"]

    def make_name():
        return f"{rng.choice(first_names)} {rng.choice(last_names)} {rng.choice(last_names)}"

    pool = set()
    while len(pool) < pool_size:
        pool.add(make_name())
    pool = sorted(pool)

    active = set(rng.sample(pool, k=int(pool_size * 0.7)))
    waiting = [n for n in pool if n not in active]
    rng.shuffle(waiting)

    reports = []
    for i in range(n_reports):
        year = 2025 + (i // 12)
        month = (i % 12) + 1
        report_date = f"{year:04d}-{month:02d}-01"

        if i > 0:
            leaving = set(rng.sample(sorted(active), k=max(1, int(len(active) * 0.15))))
            active.difference_update(leaving)
            for _ in range(rng.randint(1, 3)):
                if waiting:
                    active.add(waiting.pop())

        clients = {}
        for name in active:
            properties = []
            for _p in range(rng.choices([1, 2], weights=[80, 20])[0]):
                parcels = []
                n_parcels = rng.randint(1, 4)
                for pc in range(n_parcels):
                    day = rng.randint(1, 28)
                    parcels.append({
                        "parcela": f"{pc + 1}/{n_parcels}",
                        "vencimento": f"{day:02d}/{month:02d}/{year}",
                        "vencimento_full": f"{year:04d}-{month:02d}-{day:02d}",
                        "valor": round(rng.uniform(300, 5000), 2),
                    })
                properties.append({
                    "venda_id": f"V{rng.randint(10000, 99999)}",
                    "identifier": f"Lote {rng.randint(1, 50)}",
                    "parcels": parcels,
                })
            clients[name] = {
                "cpf_cnpj": f"{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}-{rng.randint(10, 99)}",
                "cel": f"119{rng.randint(10000000, 99999999)}",
                "email": "",
                "properties": properties,
            }
        reports.append((f"Relatorio Sintetico {i + 1}", report_date, clients))
    return reports


class GoldenKPITests(unittest.TestCase):
    """Fixture pequena e calculada à mão (2 relatórios, 3 clientes) que trava
    o comportamento ATUAL de dedup/recovery_rate/somas. Existe para que os
    itens ainda pendentes de decisão humana (K1 dedup, K2 normalização de
    nome, K6 semântica de recovery_rate, K7 precisão monetária) tenham um
    "antes" claro para comparar contra o "depois" quando forem aprovados."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="inad_golden_")
        _fresh_db(self.tmpdir)

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_recovery_rate_and_totals(self):
        # Relatório 1: Ana (R$1000) e Bruno (R$2000)
        _import_report("Relatorio 1", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "111", "cel": "11987654321", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "10/01/2026", "valor": 1000.0},
                ]}],
            },
            "BRUNO COSTA": {
                "cpf_cnpj": "222", "cel": "11911112222", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "10/01/2026", "valor": 2000.0},
                ]}],
            },
        })
        # Relatório 2: Ana continua, Bruno pagou/sumiu, Carla é nova (R$1500)
        _import_report("Relatorio 2", "2026-02-01", {
            "ANA SILVA": {
                "cpf_cnpj": "111", "cel": "11987654321", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 1000.0},
                ]}],
            },
            "CARLA DIAS": {
                "cpf_cnpj": "333", "cel": "11933334444", "email": "",
                "properties": [{"venda_id": "V3", "identifier": "Lote 3", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 1500.0},
                ]}],
            },
        })

        kpis = run.get_kpis_data(None)
        evo = {e["report_name"]: e for e in kpis["evolution"]}

        self.assertEqual(evo["Relatorio 1"]["clients"], 2)
        self.assertEqual(evo["Relatorio 1"]["parcels"], 2)
        self.assertEqual(evo["Relatorio 1"]["total_value"], 3000.0)

        self.assertEqual(evo["Relatorio 2"]["clients"], 2)
        self.assertEqual(evo["Relatorio 2"]["parcels"], 2)
        self.assertEqual(evo["Relatorio 2"]["total_value"], 2500.0)

        self.assertEqual(len(kpis["transitions"]), 1)
        trans = kpis["transitions"][0]
        self.assertEqual(trans["from_report"], "Relatorio 1")
        self.assertEqual(trans["to_report"], "Relatorio 2")
        self.assertEqual(trans["total_clients"], 2)
        self.assertEqual(trans["recovered_clients"], 1)   # só Bruno some (regra atual: sumiu = recuperado)
        self.assertEqual(trans["recovery_rate"], 50.0)     # 1 de 2 = 50%

        # Reconciliação: soma dos segmentos (novo+antigo) == total, em todo relatório
        analytics = run.get_analytics_data(cutoff_last_n=1)
        for s in analytics["series"]:
            self.assertEqual(
                s["total"]["clients"], s["novo"]["clients"] + s["antigo"]["clients"],
                f"reconciliação de clients falhou em {s['report_name']}",
            )
            self.assertAlmostEqual(
                s["total"]["total_value"], s["novo"]["total_value"] + s["antigo"]["total_value"],
                places=2, msg=f"reconciliação de total_value falhou em {s['report_name']}",
            )

    def test_explicit_report_ids_match_default_path(self):
        """K4: o caminho com report_ids explícito deve concordar com o
        caminho default (sem filtro) quando a seleção é equivalente — antes,
        o caminho explícito ignorava a deduplicação por report_date que o
        caminho default aplicava, podendo divergir."""
        id1 = _import_report("Relatorio A", "2026-01-01", {
            "DANIEL PEREIRA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "2026-01-10",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
        })
        id2 = _import_report("Relatorio B", "2026-02-01", {
            "DANIEL PEREIRA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "2026-02-10",
                     "vencimento_full": "2026-02-10", "valor": 500.0},
                ]}],
            },
            "ELIANE ROCHA": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "2026-02-10",
                     "vencimento_full": "2026-02-10", "valor": 700.0},
                ]}],
            },
        })

        default_kpis = run.get_kpis_data(None)
        explicit_kpis = run.get_kpis_data([id1, id2])

        self.assertEqual(default_kpis["evolution"], explicit_kpis["evolution"])
        self.assertEqual(default_kpis["transitions"], explicit_kpis["transitions"])

    def test_report_date_br_is_normalized_to_iso(self):
        _import_report("Relatorio BR", "05/03/2026", {})
        row = run.get_conn().cursor().execute(
            "SELECT report_date FROM reports WHERE report_name = ?", ("Relatorio BR",)
        ).fetchone()
        self.assertEqual(row[0], "2026-03-05")

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            _import_report("Relatorio Invalido", "31/02/2026", {})

    def test_name_normalization_treats_variant_spelling_as_same_client(self):
        """K2: acento/caixa/espaço não podem fazer um cliente aparecer como
        'recuperado' (sumiu) só porque a grafia mudou de relatório pra
        relatório — critério de aceitação do item K2 do HANDOFF."""
        _import_report("Relatorio 1", "2026-01-01", {
            "JOSÉ DA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 800.0},
                ]}],
            },
        })
        _import_report("Relatorio 2", "2026-02-01", {
            # mesma pessoa: sem acento, caixa diferente, espaço extra no fim
            "Jose da Silva ": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 800.0},
                ]}],
            },
        })

        kpis = run.get_kpis_data(None)
        self.assertEqual(len(kpis["transitions"]), 1)
        trans = kpis["transitions"][0]
        self.assertEqual(trans["total_clients"], 1)
        self.assertEqual(trans["recovered_clients"], 0)   # não sumiu — é o mesmo cliente
        self.assertEqual(trans["recovery_rate"], 0.0)

        analytics = run.get_analytics_data(cutoff_last_n=1)
        self.assertEqual(len(analytics["transitions"]), 1)
        a_trans = analytics["transitions"][0]
        self.assertEqual(a_trans["total_clients"], 1)
        self.assertEqual(a_trans["recovered_clients"], 0)
        self.assertEqual(a_trans["recovery_rate"], 0.0)

    def test_name_normalization_applies_to_kpi_exclusions(self):
        """K2: exclusão de KPI cadastrada com uma grafia deve excluir o
        cliente mesmo se a grafia no relatório for diferente (acento/caixa)."""
        _import_report("Relatorio 1", "2026-01-01", {
            "MARIA DA CONCEIÇÃO": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
            "OUTRO CLIENTE": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 300.0},
                ]}],
            },
        })
        conn = run.get_conn()
        conn.execute(
            "INSERT INTO kpi_exclusions (client_name) VALUES (?)",
            ("maria da conceicao",),   # grafia diferente da armazenada em clients.name
        )
        conn.commit()

        kpis = run.get_kpis_data(None)
        evo = kpis["evolution"][0]
        self.assertEqual(evo["clients"], 1)          # só "OUTRO CLIENTE" conta
        self.assertEqual(evo["total_value"], 300.0)

    def test_name_normalization_applies_to_reentry_tracking(self):
        """K2: reentradas/timeline (usadas em fila, perfil e worklist) devem
        unir a presença do cliente entre relatórios mesmo com grafia
        diferente, em vez de tratar cada grafia como um cliente à parte."""
        _import_report("Relatorio 1", "2026-01-01", {
            "APARECIDA GONÇALVES": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 400.0},
                ]}],
            },
        })
        _import_report("Relatorio 2", "2026-02-01", {
            "OUTRO CLIENTE": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 100.0},
                ]}],
            },
        })
        _import_report("Relatorio 3", "2026-03-01", {
            # mesma pessoa do Relatorio 1, sem cedilha/acento
            "Aparecida Goncalves": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/03/2026",
                     "vencimento_full": "2026-03-10", "valor": 400.0},
                ]}],
            },
        })

        reentries_map = run._calculate_reentries(run.get_conn().cursor())
        key = run._normalize_name("APARECIDA GONÇALVES")
        self.assertEqual(key, run._normalize_name("Aparecida Goncalves"))
        info = reentries_map[key]
        self.assertEqual(info["reentries"], 1)
        self.assertTrue(info["currently_present"])
        self.assertEqual(info["first_seen"], "2026-01-01")
        self.assertEqual(
            [t["present"] for t in info["timeline"]], [True, False, True]
        )

    def test_monetary_totals_are_cent_exact(self):
        """K7: soma de parcelas com valores classicamente sujeitos a drift de
        ponto flutuante em float puro (0.10 + 0.20 == 0.30000000000000004)
        precisa bater EXATAMENTE ao centavo em todo agregado monetário —
        cliente, evolução de KPI e série/segmentos de Analytics (novo e
        antigo, e a soma novo+antigo). Critério de aceitação original do K7
        (HANDOFF.md): soma de centavos inteiros, sem drift."""
        self.assertNotEqual(0.10 + 0.20, 0.30)  # sanity: o drift clássico existe em float puro

        def _drift_prone_parcels(n_a, n_b, prefix):
            parcels = []
            for i in range(n_a):
                parcels.append({"parcela": f"{prefix}A{i}", "vencimento": "10/01/2026",
                                 "vencimento_full": "2026-01-10", "valor": 0.10})
            for i in range(n_b):
                parcels.append({"parcela": f"{prefix}B{i}", "vencimento": "10/01/2026",
                                 "vencimento_full": "2026-01-10", "valor": 0.20})
            return parcels

        _import_report("Relatorio 1", "2026-01-01", {
            "CLIENTE ANTIGO": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1",
                                 "parcels": _drift_prone_parcels(7, 7, "R1")}],
            },
        })
        _import_report("Relatorio 2", "2026-02-01", {
            "CLIENTE ANTIGO": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1",
                                 "parcels": _drift_prone_parcels(7, 7, "R2")}],
            },
            "CLIENTE NOVO": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2",
                                 "parcels": _drift_prone_parcels(11, 3, "R2N")}],
            },
        })

        cursor = run.get_conn().cursor()
        report2_id = run._dedup_latest_report_id(cursor)
        cf = run._client_financials(cursor, report2_id, "2026-02-01")
        self.assertEqual(cf["CLIENTE ANTIGO"]["total_owed"], 2.10)   # 7*0.10 + 7*0.20
        self.assertEqual(cf["CLIENTE NOVO"]["total_owed"], 1.70)     # 11*0.10 + 3*0.20

        kpis = run.get_kpis_data(None)
        evo_by_name = {e["report_name"]: e for e in kpis["evolution"]}
        self.assertEqual(evo_by_name["Relatorio 2"]["total_value"], 3.80)  # 2.10 + 1.70

        analytics = run.get_analytics_data(cutoff_last_n=1)
        last = analytics["series"][-1]
        self.assertEqual(last["novo"]["total_value"], 1.70)
        self.assertEqual(last["antigo"]["total_value"], 2.10)
        self.assertEqual(last["total"]["total_value"], 3.80)   # soma em centavos — sem double-rounding

    def test_confirmed_recovery_reported_alongside_recovery_rate(self):
        """K6 (opção C, decisão do responsável): recovery_rate ('saiu do
        relatório') continua existindo sem mudança de comportamento — mas
        recovery_rate_confirmed passa a reportar, ao lado, a fração desses
        que tem um desfecho 'pagou' registrado. As duas convivem; nenhuma
        substitui a outra."""
        _import_report("Relatorio 1", "2026-01-01", {
            "ANA PERMANECE": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 100.0},
                ]}],
            },
            "BRUNO SEM_OUTCOME": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 200.0},
                ]}],
            },
            "CARLA PAGOU": {
                "cpf_cnpj": "3", "cel": "", "email": "",
                "properties": [{"venda_id": "V3", "identifier": "Lote 3", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 300.0},
                ]}],
            },
        })
        _import_report("Relatorio 2", "2026-02-01", {
            "ANA PERMANECE": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 100.0},
                ]}],
            },
            # BRUNO e CARLA somem do Relatorio 2 — mesmo sinal bruto (recovery_rate),
            # mas só CARLA tem um desfecho 'pagou' registrado.
        })
        conn = run.get_conn()
        conn.execute(
            "INSERT INTO contact_outcomes (client_name, outcome) VALUES (?, 'pagou')",
            ("CARLA PAGOU",),
        )
        conn.commit()

        kpis = run.get_kpis_data(None)
        self.assertEqual(len(kpis["transitions"]), 1)
        trans = kpis["transitions"][0]
        self.assertEqual(trans["total_clients"], 3)
        self.assertEqual(trans["recovered_clients"], 2)              # Bruno + Carla sumiram
        self.assertEqual(trans["recovery_rate"], 66.7)                # inalterado (sinal amplo)
        self.assertEqual(trans["recovered_confirmed_clients"], 1)     # só Carla
        self.assertEqual(trans["recovery_rate_confirmed"], 33.3)

        analytics = run.get_analytics_data(cutoff_last_n=1)
        a_trans = analytics["transitions"][0]
        self.assertEqual(a_trans["recovered_clients"], 2)
        self.assertEqual(a_trans["recovery_rate"], 66.7)
        self.assertEqual(a_trans["recovered_confirmed_clients"], 1)
        self.assertEqual(a_trans["recovery_rate_confirmed"], 33.3)

    def test_access_audit_logs_profile_reads(self):
        """S6: _log_access() registra quem consultou o perfil (PII) de qual
        cliente e quando — decisão do responsável: tabela no banco
        (queryable via GET /api/audit), não um arquivo de log."""
        _import_report("Relatorio 1", "2026-01-01", {
            "FULANO DE TAL": {
                "cpf_cnpj": "123.456.789-00", "cel": "11999998888", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
        })

        conn = run.get_conn()
        run._log_access(conn, "operador_teste", "FULANO DE TAL")
        run._log_access(conn, "operador_teste", "FULANO DE TAL")
        run._log_access(conn, "outro_operador", "OUTRO CLIENTE")

        rows = conn.cursor().execute(
            "SELECT operator, client_name FROM access_audit ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "operador_teste")
        self.assertEqual(rows[0][1], "FULANO DE TAL")

        only_fulano = conn.cursor().execute(
            "SELECT COUNT(*) FROM access_audit WHERE client_name = ?", ("FULANO DE TAL",)
        ).fetchone()[0]
        self.assertEqual(only_fulano, 2)


class LargeDatasetReconciliationTests(unittest.TestCase):
    """Gera uma série sintética determinística de relatórios (random.Random(42),
    sem depender de nenhum script externo) com churn de clientes entre meses,
    e verifica, para TODOS os relatórios, que soma dos segmentos (novo+antigo)
    == total em clientes/parcelas/valor. É uma invariante estrutural, não
    depende de números calculados à mão — por isso não é invalidada por
    K1/K2 (dedup e normalização de nome), que só mudam QUAIS clientes entram
    em cada conta, nunca se a soma das partes bate com o todo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="inad_synth_recon_")
        _fresh_db(self.tmpdir, name="synth_recon.db")
        rng = random.Random(42)
        for name, date, clients in _generate_synthetic_reports(rng):
            _import_report(name, date, clients)

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_segment_totals_reconcile_for_every_report(self):
        analytics = run.get_analytics_data(cutoff_last_n=1)
        self.assertTrue(analytics["series"], "esperava pelo menos 1 relatório no dataset sintético")
        for s in analytics["series"]:
            expected_clients = s["novo"]["clients"] + s["antigo"]["clients"]
            expected_parcels = s["novo"]["parcels"] + s["antigo"]["parcels"]
            expected_value = round(s["novo"]["total_value"] + s["antigo"]["total_value"], 2)
            self.assertEqual(s["total"]["clients"], expected_clients,
                              f"{s['report_name']}: clients não reconcilia")
            self.assertEqual(s["total"]["parcels"], expected_parcels,
                              f"{s['report_name']}: parcels não reconcilia")
            self.assertAlmostEqual(s["total"]["total_value"], expected_value, places=2,
                                    msg=f"{s['report_name']}: total_value não reconcilia")

    def test_kpis_evolution_matches_analytics_totals_per_report(self):
        """Reconciliação cruzada: get_kpis_data() e get_analytics_data() devem
        concordar sobre clients/parcels/total_value do MESMO relatório, ambos
        no caminho default (sem report_ids explícito — não exercita o bug K4,
        que só se manifesta quando report_ids é passado)."""
        kpis = run.get_kpis_data(None)
        analytics = run.get_analytics_data(cutoff_last_n=1)
        evo_by_id = {e["report_id"]: e for e in kpis["evolution"]}
        for s in analytics["series"]:
            evo = evo_by_id.get(s["report_id"])
            self.assertIsNotNone(evo, f"relatório {s['report_id']} ausente em get_kpis_data")
            self.assertEqual(evo["clients"], s["total"]["clients"])
            self.assertEqual(evo["parcels"], s["total"]["parcels"])
            self.assertAlmostEqual(evo["total_value"], s["total"]["total_value"], places=2)


class ReformaKPITests(unittest.TestCase):
    """Golden tests do INSTRUCOES_REFORMA_KPI.md §6 — completude/escopo de
    relatório, separação factual×operacional e cobertura do dicionário de
    KPIs. Datasets pequenos, calculados à mão, num banco temporário isolado."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="inad_reforma_")
        _fresh_db(self.tmpdir)

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_partial_report_excluded_from_temporal_kpis_but_not_from_worklist(self):
        """Completo -> parcial (só 1-3 parcelas) -> a recuperação não conta
        os clientes ausentes do parcial (ele nem entra na comparação); o
        parcial aparece em meta.relatorios_excluidos com o motivo; os dados
        intra-relatório do parcial (all_evolution) continuam disponíveis."""
        _import_report("Relatorio Completo", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 1000.0},
                ]}],
            },
            "BRUNO COSTA": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 2000.0},
                ]}],
            },
        }, escopo="completo")

        partial_id = _import_report("Relatorio Parcial (1-3 parcelas)", "2026-02-01", {
            # Só Ana aparece — um extrato filtrado só mostraria isso; se
            # contasse como "recuperação", Bruno pareceria ter sumido/pago
            # quando na verdade só está fora do recorte do relatório.
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 1000.0},
                ]}],
            },
        }, escopo="parcial", escopo_motivo="filtrado 1-3 parcelas")

        kpis = run.get_kpis_data(None)

        # Nenhum relatório completo seguinte -> nada para comparar ainda.
        self.assertEqual(kpis["transitions"], [])
        self.assertTrue(kpis["meta"]["dados_insuficientes_para_transicoes"])
        self.assertEqual(kpis["meta"]["relatorios_completos_considerados"], 1)

        excluidos = kpis["meta"]["relatorios_excluidos"]
        self.assertEqual(len(excluidos), 1)
        self.assertEqual(excluidos[0]["id"], partial_id)
        self.assertEqual(excluidos[0]["escopo"], "parcial")
        self.assertEqual(excluidos[0]["escopo_motivo"], "filtrado 1-3 parcelas")

        # Dados intra-relatório do parcial continuam acessíveis (não é
        # apagado nem escondido — só fica fora de evolution/transitions).
        all_evo_by_id = {e["report_id"]: e for e in kpis["all_evolution"]}
        self.assertIn(partial_id, all_evo_by_id)
        self.assertEqual(all_evo_by_id[partial_id]["clients"], 1)
        self.assertEqual(all_evo_by_id[partial_id]["total_value"], 1000.0)
        self.assertEqual(all_evo_by_id[partial_id]["escopo"], "parcial")

        # Analytics segue a mesma regra (mesma fonte única).
        analytics = run.get_analytics_data(cutoff_last_n=1)
        self.assertEqual(analytics["transitions"], [])
        self.assertTrue(analytics["meta"]["dados_insuficientes_para_transicoes"])
        self.assertEqual(len(analytics["meta"]["relatorios_excluidos"]), 1)
        self.assertEqual(analytics["meta"]["relatorios_excluidos"][0]["id"], partial_id)

    def test_fewer_than_two_completos_never_crashes_returns_dados_insuficientes(self):
        """<2 relatórios completos -> KPIs temporais voltam 'dados
        insuficientes' (metadado explícito), nunca uma exceção nem um 0/vazio
        silencioso sem explicação. Cobre 0 e 1 relatório completo."""
        # 0 relatórios
        kpis_empty = run.get_kpis_data(None)
        self.assertEqual(kpis_empty["evolution"], [])
        self.assertEqual(kpis_empty["transitions"], [])
        self.assertTrue(kpis_empty["meta"]["dados_insuficientes_para_transicoes"])

        analytics_empty = run.get_analytics_data(cutoff_last_n=1)
        self.assertEqual(analytics_empty["series"], [])
        self.assertTrue(analytics_empty["meta"]["dados_insuficientes_para_transicoes"])

        # 1 relatório completo
        _import_report("Único Completo", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
        }, escopo="completo")

        kpis_one = run.get_kpis_data(None)
        self.assertEqual(len(kpis_one["evolution"]), 1)   # série existe (1 ponto)
        self.assertEqual(kpis_one["transitions"], [])      # mas sem transição possível
        self.assertTrue(kpis_one["meta"]["dados_insuficientes_para_transicoes"])
        self.assertEqual(kpis_one["meta"]["relatorios_completos_considerados"], 1)

    def test_heuristic_flags_low_parcela_count_as_suspect(self):
        """Heurística '<=3 parcelas': mesmo com escopo='completo' declarado
        pelo usuário, se TODOS os clientes têm poucas parcelas o relatório é
        rebaixado a 'nao_confirmado' com o motivo registrado — proteção
        contra declaração equivocada, nunca promove a 'completo' sozinha."""
        report_id = _import_report("Relatorio Suspeito", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/2", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
            "BRUNO COSTA": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/2", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 300.0},
                ]}],
            },
        }, escopo="completo", run_heuristic=True)

        row = run.get_conn().cursor().execute(
            "SELECT escopo, escopo_motivo, escopo_origem FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        self.assertEqual(row[0], "nao_confirmado")
        self.assertIn("filtro de parcelas", row[1])
        self.assertEqual(row[2], "heuristica")

        # E como consequência, some de evolution/transitions.
        kpis = run.get_kpis_data(None)
        self.assertEqual(kpis["evolution"], [])
        self.assertEqual(len(kpis["meta"]["relatorios_excluidos"]), 1)

    def test_heuristic_never_overrides_explicit_partial_declaration(self):
        """Um relatório já declarado 'parcial' pelo usuário não é mexido pela
        heurística (ela só atua sobre 'completo'/'nao_confirmado')."""
        report_id = _import_report("Relatorio Parcial Declarado", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
        }, escopo="parcial", escopo_motivo="corte manual do usuário", run_heuristic=True)

        row = run.get_conn().cursor().execute(
            "SELECT escopo, escopo_motivo FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        self.assertEqual(row[0], "parcial")
        self.assertEqual(row[1], "corte manual do usuário")   # motivo do usuário preservado

    def test_operational_outcome_does_not_change_factual_kpis(self):
        """Separação factual x operacional: registrar um desfecho 'pagou'
        (Universo B) não pode alterar nenhum KPI factual (total, nº de
        clientes, recovery_rate bruta) — só o operacional (recovery_rate_
        confirmed) muda. Mesma garantia de test_confirmed_recovery_*, mas
        aqui comparando o ANTES e o DEPOIS byte a byte."""
        _import_report("Relatorio 1", "2026-01-01", {
            "ANA PERMANECE": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 100.0},
                ]}],
            },
            "CARLA PAGOU": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [{"venda_id": "V2", "identifier": "Lote 2", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 300.0},
                ]}],
            },
        })
        _import_report("Relatorio 2", "2026-02-01", {
            "ANA PERMANECE": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 100.0},
                ]}],
            },
        })

        def _factual_snapshot():
            kpis = run.get_kpis_data(None)
            evo = [{k: v for k, v in e.items() if k != "escopo_motivo"} for e in kpis["evolution"]]
            trans = [{k: v for k, v in t.items()
                       if k not in ("recovered_confirmed_clients", "recovery_rate_confirmed")}
                      for t in kpis["transitions"]]
            return evo, trans

        evo_before, trans_before = _factual_snapshot()

        conn = run.get_conn()
        conn.execute(
            "INSERT INTO contact_outcomes (client_name, outcome) VALUES (?, 'pagou')",
            ("CARLA PAGOU",),
        )
        conn.commit()

        evo_after, trans_after = _factual_snapshot()

        self.assertEqual(evo_before, evo_after,
                          "registrar um desfecho não pode alterar os KPIs factuais de evolução")
        self.assertEqual(trans_before, trans_after,
                          "registrar um desfecho não pode alterar os campos factuais de transições")

        # O operacional, por sua vez, DEVE mudar.
        kpis_after = run.get_kpis_data(None)
        self.assertEqual(kpis_after["transitions"][0]["recovered_confirmed_clients"], 1)

    def test_reclassify_escopo_recalculates_temporal_kpis(self):
        """Reclassificar o escopo de um relatório (equivalente ao POST
        /api/reports/<id>/escopo) muda imediatamente quem entra em
        evolution/transitions — sem precisar reimportar nada."""
        _import_report("Relatorio 1", "2026-01-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/01/2026",
                     "vencimento_full": "2026-01-10", "valor": 500.0},
                ]}],
            },
        }, escopo="completo")
        report2_id = _import_report("Relatorio 2 (dúvida)", "2026-02-01", {
            "ANA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "2/2", "vencimento": "10/02/2026",
                     "vencimento_full": "2026-02-10", "valor": 500.0},
                ]}],
            },
        }, escopo="nao_confirmado")

        kpis_before = run.get_kpis_data(None)
        self.assertEqual(len(kpis_before["evolution"]), 1)   # só o Relatorio 1
        self.assertTrue(kpis_before["meta"]["dados_insuficientes_para_transicoes"])

        # Reclassifica (mesma operação SQL do endpoint POST /api/reports/<id>/escopo).
        conn = run.get_conn()
        conn.execute(
            "UPDATE reports SET escopo = 'completo', escopo_motivo = '', "
            "escopo_origem = 'declarado_usuario' WHERE id = ?",
            (report2_id,),
        )
        conn.commit()

        kpis_after = run.get_kpis_data(None)
        self.assertEqual(len(kpis_after["evolution"]), 2)
        self.assertFalse(kpis_after["meta"]["dados_insuficientes_para_transicoes"])
        self.assertEqual(len(kpis_after["transitions"]), 1)
        self.assertEqual(kpis_after["transitions"][0]["recovery_rate"], 0.0)  # Ana continua

    def test_kpi_dictionary_covers_every_kpi_referenced_in_the_ui(self):
        """Nenhum KPI órfão: todo id passado para toggleKpiInfo(...) no HTML/JS
        do frontend precisa ter uma entrada correspondente em KPI_DICIONARIO
        (senão o ícone '?' abriria um tooltip vazio, sem legenda)."""
        import re
        dict_ids = {k["id"] for k in run.KPI_DICIONARIO}
        self.assertTrue(dict_ids, "KPI_DICIONARIO não pode estar vazio")

        referenced_ids = set()
        pattern = re.compile(r"toggleKpiInfo\([^,]+,\s*'([a-zA-Z0-9_]+)'\)")
        for fname in ("inad_template.html", "inad_analytics.html", "analytics.js"):
            fpath = os.path.join(PROJECT_DIR, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            found = pattern.findall(content)
            referenced_ids.update(found)

        self.assertTrue(referenced_ids, "nenhuma chamada a toggleKpiInfo() encontrada no frontend")
        orphans = referenced_ids - dict_ids
        self.assertEqual(
            orphans, set(),
            f"KPI(s) referenciado(s) na UI sem entrada no dicionário: {sorted(orphans)}",
        )

    def test_kpi_dictionary_endpoint_shape(self):
        """Toda entrada do dicionário tem os campos exigidos por
        INSTRUCOES_REFORMA_KPI.md §4.1 e um universo válido."""
        for item in run.KPI_DICIONARIO:
            for field in ("id", "nome", "definicao", "formula", "universo", "observacoes"):
                self.assertIn(field, item, f"KPI {item.get('id')} sem campo '{field}'")
            self.assertIn(item["universo"], ("factual", "operacional", "derivado"))


if __name__ == "__main__":
    unittest.main()
