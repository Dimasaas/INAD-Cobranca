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

    def test_report_date_br_is_normalized_to_iso(self):
        _import_report("Relatorio BR", "05/03/2026", {})
        row = run.get_conn().cursor().execute(
            "SELECT report_date FROM reports WHERE report_name = ?", ("Relatorio BR",)
        ).fetchone()
        self.assertEqual(row[0], "2026-03-05")

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            _import_report("Relatorio Invalido", "31/02/2026", {})


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
