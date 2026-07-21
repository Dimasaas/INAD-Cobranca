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


def _import_report(name, date, clients):
    """Insere um relatório + árvore de clientes usando o caminho real de
    ingestão de run.py (_normalize_date/_insert_clients), igual à API."""
    conn = run.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
        (name, run._normalize_date(date)),
    )
    report_id = cursor.lastrowid
    run._insert_clients(cursor, report_id, clients)
    conn.commit()
    return report_id


class _FakeHandler:
    """Handler mínimo para exercitar run._authenticate() sem subir um servidor
    HTTP: só precisa de .headers.get('X-INAD-Token') e .path (ver
    run._request_token/_authenticate)."""
    def __init__(self, token="", path="/api/reports"):
        self.path = path
        self.headers = {"X-INAD-Token": token} if token else {}


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

    def test_name_normalization_applies_to_novos_pre_juridico(self):
        """K2 (gap residual documentado no HANDOFF): a detecção de 'novos
        pré-jurídico' em _get_worklist_data() comparava prev_cf.get(name) com
        o nome exato do relatório anterior — se a grafia mudasse entre os
        dois relatórios mais recentes, a transição podia não ser detectada.
        Mesmo cliente, grafia diferente entre os dois relatórios, tem que
        aparecer em 'novos_pre_juridico' quando cruza o corte de 121 dias."""
        _import_report("Relatorio 1", "2026-01-01", {
            # 31 dias de atraso em 2026-01-01: ainda não é pré-jurídico (<=120)
            "JOSE DA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "01/12/2025",
                     "vencimento_full": "2025-12-01", "valor": 800.0},
                ]}],
            },
        })
        _import_report("Relatorio 2", "2026-06-01", {
            # mesma pessoa (identidade via K2), com acento; 151 dias de atraso
            # em 2026-06-01: cruzou o corte de pré-jurídico (>120)
            "JOSÉ DA SILVA": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [{"venda_id": "V1", "identifier": "Lote 1", "parcels": [
                    {"parcela": "1/1", "vencimento": "01/01/2026",
                     "vencimento_full": "2026-01-01", "valor": 800.0},
                ]}],
            },
        })

        cursor = run.get_conn().cursor()
        worklist = run._get_worklist_data(cursor, "2026-06-01")
        names = {item["name"] for item in worklist["novos_pre_juridico"]}
        self.assertEqual(names, {"JOSÉ DA SILVA"})

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

    def test_read_only_operator_is_authenticated_but_cannot_write(self):
        """Papel somente-leitura: _authenticate() reconhece o token do operador
        (autorizado=True) mas sinaliza pode_escrever=False — do_POST/do_DELETE
        usam esse sinal para responder 403. Operador de escrita e o bind
        loopback continuam com pode_escrever=True (sem regressão)."""
        rw_token = run._add_operator("Operador Escrita")
        ro_token = run._add_operator("Operador Leitura", can_write=False)

        # Força o caminho de autenticação (fora de loopback), restaurando o
        # HOST depois — em loopback _authenticate() curto-circuita antes do token.
        original_host = run.HOST
        run.HOST = "192.168.0.10"
        try:
            ok, name, can_write = run._authenticate(_FakeHandler(rw_token))
            self.assertTrue(ok)
            self.assertEqual(name, "Operador Escrita")
            self.assertTrue(can_write)

            ok, name, can_write = run._authenticate(_FakeHandler(ro_token))
            self.assertTrue(ok)                       # autenticado normalmente
            self.assertEqual(name, "Operador Leitura")
            self.assertFalse(can_write)               # mas não pode escrever

            ok, name, can_write = run._authenticate(_FakeHandler("token-invalido"))
            self.assertFalse(ok)
            self.assertFalse(can_write)
        finally:
            run.HOST = original_host

        # Bind loopback (padrão local): dono da máquina sempre pode escrever,
        # sem exigir token nem papel.
        self.assertTrue(run._is_loopback_bind())
        ok, name, can_write = run._authenticate(_FakeHandler(""))
        self.assertTrue(ok)
        self.assertTrue(can_write)


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


if __name__ == "__main__":
    unittest.main()
