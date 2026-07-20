"""
Testes golden de corretude de KPI + reconciliação (Fase 0-B, pré-requisito).

Roda inteiramente sobre bancos SQLite temporários — nunca toca em
inad_database.db nem em inad_demo.db.

Uso:
  python -m unittest discover -s tests -v
  python tests/test_golden_kpis.py
"""

import os
import sys
import shutil
import tempfile
import unittest

os.environ.setdefault("INAD_HEADLESS", "1")
os.environ.setdefault("INAD_DEMO", "1")

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import run  # noqa: E402


def _fresh_db(tmp_dir, name="test.db"):
    """Redireciona run.DB_PATH para um arquivo temporário isolado e cria o schema.
    Em modo demo, run.init_db() auto-popula dados fictícios se a tabela
    'reports' estiver vazia (para o botão Modo Demo funcionar sem passos
    manuais) — isso é indesejável nestes testes, que controlam explicitamente
    o que entra no banco, então a auto-população é sempre limpa em seguida."""
    if hasattr(run._local, "conn") and run._local.conn is not None:
        run._local.conn.close()
        run._local.conn = None
    run.DB_PATH = os.path.join(tmp_dir, name)
    run.init_db()
    conn = run.get_conn()
    conn.execute("DELETE FROM reports")  # cascade limpa clients/properties/parcels
    conn.commit()


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

    def _import_report(self, name, date, clients):
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

    def test_recovery_rate_and_totals(self):
        # Relatório 1: Ana (R$1000) e Bruno (R$2000)
        self._import_report("Relatorio 1", "2026-01-01", {
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
        self._import_report("Relatorio 2", "2026-02-01", {
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
        self._import_report("Relatorio BR", "05/03/2026", {})
        row = run.get_conn().cursor().execute(
            "SELECT report_date FROM reports WHERE report_name = ?", ("Relatorio BR",)
        ).fetchone()
        self.assertEqual(row[0], "2026-03-05")

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self._import_report("Relatorio Invalido", "31/02/2026", {})


class DemoDataReconciliationTests(unittest.TestCase):
    """Roda o gerador de dados demo (determinístico, seed=42 — ver
    generate_demo_data.py) contra um banco temporário isolado e verifica, para
    TODOS os relatórios gerados, que soma dos segmentos (novo+antigo) == total
    em clientes/parcelas/valor. É uma invariante estrutural, não depende de
    números calculados à mão — por isso não é invalidada por K1/K2 (dedup e
    normalização de nome), que só mudam QUAIS clientes entram em cada conta,
    nunca se a soma das partes bate com o todo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="inad_demo_recon_")
        _fresh_db(self.tmpdir, name="demo_recon.db")
        import generate_demo_data
        generate_demo_data.generate(seed=42)

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_segment_totals_reconcile_for_every_report(self):
        analytics = run.get_analytics_data(cutoff_last_n=1)
        self.assertTrue(analytics["series"], "esperava pelo menos 1 relatório nos dados demo")
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
