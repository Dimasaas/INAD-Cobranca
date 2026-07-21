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
import sqlite3
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

    def test_name_normalization_treats_spelling_variants_as_same_client(self):
        """K2: acento/caixa/espaço não podem fazer o mesmo cliente contar como
        dois clientes distintos. Importa "JOSÉ DA SILVA" no relatório 1 e
        "Jose da Silva " (sem acento, minúsculo, espaço à direita) no
        relatório 2 e confirma que:
          1) em recovery_rate, o cliente NÃO é contado como "recuperado"
             (sumiu) apenas por causa da grafia diferente;
          2) uma exclusão cadastrada como "jose da silva" exclui o cliente
             dos KPIs independentemente da grafia armazenada em `clients`.
        """
        _import_report("Relatorio Norm 1", "2026-03-01", {
            "JOSÉ DA SILVA": {
                "cpf_cnpj": "444", "cel": "11955556666", "email": "",
                "properties": [{"venda_id": "V10", "identifier": "Lote 10", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/03/2026",
                     "vencimento_full": "2026-03-10", "valor": 800.0},
                ]}],
            },
        })
        _import_report("Relatorio Norm 2", "2026-04-01", {
            "Jose da Silva ": {
                "cpf_cnpj": "444", "cel": "11955556666", "email": "",
                "properties": [{"venda_id": "V10", "identifier": "Lote 10", "parcels": [
                    {"parcela": "1/1", "vencimento": "10/04/2026",
                     "vencimento_full": "2026-04-10", "valor": 800.0},
                ]}],
            },
        })

        kpis = run.get_kpis_data(None)
        trans_by_from = {t["from_report"]: t for t in kpis["transitions"]}
        trans = trans_by_from["Relatorio Norm 1"]
        self.assertEqual(trans["total_clients"], 1)
        self.assertEqual(
            trans["recovered_clients"], 0,
            "grafia diferente ('JOSÉ DA SILVA' vs 'Jose da Silva ') não deveria "
            "contar como cliente recuperado/sumido — é a mesma pessoa"
        )
        self.assertEqual(trans["recovery_rate"], 0.0)

        # kpi_exclusions: excluir com uma grafia totalmente diferente (minúscula,
        # sem acento) da armazenada em `clients` deve excluir o cliente mesmo assim.
        conn = run.get_conn()
        conn.execute("INSERT INTO kpi_exclusions (client_name) VALUES (?)", ("jose da silva",))
        conn.commit()

        kpis_excluded = run.get_kpis_data(None)
        evo_excluded = {e["report_name"]: e for e in kpis_excluded["evolution"]}
        self.assertEqual(evo_excluded["Relatorio Norm 1"]["clients"], 0)
        self.assertEqual(evo_excluded["Relatorio Norm 2"]["clients"], 0)


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


class MonetaryPrecisionTests(unittest.TestCase):
    """K7 — precisão monetária (centavos inteiros). Trava que somas de
    dinheiro usam `parcels.valor_centavos` (INTEGER — SUM em SQL é exato) e
    só convertem pra reais na apresentação, mesmo com muitos valores
    clássicos de "armadilha" de ponto flutuante binário (0.10, 0.20, 0.30,
    1.15, 0.01... nenhum destes é exatamente representável em binário)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="inad_k7_exact_")
        _fresh_db(self.tmpdir, name="k7_exact.db")

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exact_cent_sum_avoids_float_drift(self):
        """Insere dezenas de parcelas com valores clássicos de drift de
        float (0.10, 0.20, 0.30, 1.15, 2.20, 0.05, 3.33, 0.01, 9.99, 0.99)
        distribuídas em 2 clientes/3 imóveis, e confere que o total
        reportado — via _client_financials (usado por queue/summary/profile)
        E via get_kpis_data (usado pela aba KPI) — bate EXATO ao centavo com
        a soma matemática esperada, não uma aproximação."""
        client_a_values = [0.10] * 12 + [0.20] * 9 + [0.30] * 7 + [1.15] * 5 + [2.20] * 4
        client_b_values = [0.05] * 8 + [3.33] * 3 + [0.01] * 15 + [9.99] * 2 + [0.99] * 6

        def _parcels(values):
            return [
                {"parcela": f"{i + 1}/{len(values)}", "vencimento": "10/01/2026",
                 "vencimento_full": "2026-01-10", "valor": v}
                for i, v in enumerate(values)
            ]

        report_id = _import_report("Relatorio Centavos", "2026-01-01", {
            "CLIENTE A CENTAVOS": {
                "cpf_cnpj": "1", "cel": "", "email": "",
                "properties": [
                    {"venda_id": "VA1", "identifier": "Lote A1", "parcels": _parcels(client_a_values[:16])},
                    {"venda_id": "VA2", "identifier": "Lote A2", "parcels": _parcels(client_a_values[16:])},
                ],
            },
            "CLIENTE B CENTAVOS": {
                "cpf_cnpj": "2", "cel": "", "email": "",
                "properties": [
                    {"venda_id": "VB1", "identifier": "Lote B1", "parcels": _parcels(client_b_values)},
                ],
            },
        })

        # Soma exata esperada, calculada em centavos inteiros (mesma lógica
        # de conversão usada na ingestão real: int(round(valor*100))).
        expected_a_cents = sum(round(v * 100) for v in client_a_values)
        expected_b_cents = sum(round(v * 100) for v in client_b_values)
        expected_a = round(expected_a_cents / 100.0, 2)
        expected_b = round(expected_b_cents / 100.0, 2)
        expected_total = round((expected_a_cents + expected_b_cents) / 100.0, 2)

        # Via _client_financials (usado por /api/queue, /api/summary, /api/clients/profile)
        cursor = run.get_conn().cursor()
        cf = run._client_financials(cursor, report_id, "2099-12-31")
        self.assertEqual(cf["CLIENTE A CENTAVOS"]["total_owed"], expected_a)
        self.assertEqual(cf["CLIENTE B CENTAVOS"]["total_owed"], expected_b)
        self.assertEqual(cf["CLIENTE A CENTAVOS"]["total_owed_cents"], expected_a_cents)
        self.assertEqual(cf["CLIENTE B CENTAVOS"]["total_owed_cents"], expected_b_cents)

        # Via get_kpis_data (usado pela aba KPI — total_value do relatório inteiro)
        kpis = run.get_kpis_data(None)
        evo = {e["report_name"]: e for e in kpis["evolution"]}
        self.assertEqual(evo["Relatorio Centavos"]["total_value"], expected_total)

    def test_valor_centavos_migration_backfill_and_idempotent(self):
        """Monta um banco com o schema ANTIGO (pré-K7: parcels.valor REAL,
        SEM valor_centavos), roda a migração real (init_db) contra ele, e
        confere que valor_centavos foi retropreenchido corretamente
        (= round(valor*100)) — e que rodar a migração uma SEGUNDA vez é
        no-op (idempotente: valores inalterados, sem erro)."""
        tmp_dir = tempfile.mkdtemp(prefix="inad_k7_migration_")
        try:
            db_path = os.path.join(tmp_dir, "old_schema.db")

            raw = sqlite3.connect(db_path)
            raw.executescript("""
                CREATE TABLE reports (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_name TEXT    NOT NULL,
                    report_date TEXT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE clients (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id   INTEGER NOT NULL,
                    name        TEXT    NOT NULL,
                    cpf_cnpj    TEXT    DEFAULT '',
                    cel         TEXT    DEFAULT '',
                    email       TEXT    DEFAULT ''
                );
                CREATE TABLE properties (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id   INTEGER NOT NULL,
                    venda_id    TEXT    NOT NULL,
                    identifier  TEXT    NOT NULL
                );
                CREATE TABLE parcels (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id     INTEGER NOT NULL,
                    parcela         TEXT    NOT NULL,
                    vencimento      TEXT    NOT NULL,
                    vencimento_full TEXT    NOT NULL,
                    valor           REAL    DEFAULT 0.0
                );
            """)
            raw.execute(
                "INSERT INTO reports (id, report_name, report_date) VALUES (1, 'Legado', '2026-01-01')"
            )
            raw.execute("INSERT INTO clients (id, report_id, name) VALUES (1, 1, 'CLIENTE LEGADO')")
            raw.execute(
                "INSERT INTO properties (id, client_id, venda_id, identifier) VALUES (1, 1, 'V1', 'Lote 1')"
            )
            # Valores REAL do schema antigo, incluindo casos de arredondamento
            # não triviais (0.10, 1.15, 0.01, 33.33 não são exatos em binário).
            legacy_values = [10.0, 0.10, 1.15, 999.99, 0.01, 33.33]
            for i, v in enumerate(legacy_values):
                raw.execute(
                    "INSERT INTO parcels (property_id, parcela, vencimento, vencimento_full, valor) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (f"{i + 1}/{len(legacy_values)}", "10/01/2026", "2026-01-10", v),
                )
            raw.commit()
            raw.close()

            # Roda a migração real (init_db) contra este banco no schema antigo.
            if hasattr(run._local, "conn") and run._local.conn is not None:
                run._local.conn.close()
                run._local.conn = None
            run.DB_PATH = db_path
            run.init_db()

            cursor = run.get_conn().cursor()
            cols = {row[1] for row in cursor.execute("PRAGMA table_info(parcels)")}
            self.assertIn("valor_centavos", cols, "migração K7 não adicionou a coluna valor_centavos")

            rows = cursor.execute("SELECT valor, valor_centavos FROM parcels ORDER BY id").fetchall()
            self.assertEqual(len(rows), len(legacy_values))
            for (valor, valor_centavos), expected_v in zip(rows, legacy_values):
                self.assertEqual(
                    valor_centavos, round(expected_v * 100),
                    f"backfill incorreto para valor={expected_v}: esperado "
                    f"{round(expected_v * 100)} centavos, obtido {valor_centavos}"
                )
                self.assertEqual(valor, expected_v, "migração não deve alterar a coluna valor (REAL) legada")

            # Idempotência: rodar a migração de novo não deve alterar nada nem falhar.
            run.init_db()
            rows_again = cursor.execute("SELECT valor, valor_centavos FROM parcels ORDER BY id").fetchall()
            self.assertEqual(rows_again, rows, "segunda execução da migração não é idempotente")
        finally:
            if hasattr(run._local, "conn") and run._local.conn is not None:
                run._local.conn.close()
                run._local.conn = None
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
