"""
Testes de validação da integração UAU, sintaxe dos scripts e compilação do Frontend.
"""

import os
import sys
import ast
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import run


class UAUSyncTests(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None
        run.DB_PATH = os.path.join(self.tmp_dir, "test_sync.db")
        run.init_db()

    def tearDown(self):
        if hasattr(run._local, "conn") and run._local.conn is not None:
            run._local.conn.close()
            run._local.conn = None

    def test_python_syntax(self):
        """Verifica se todos os scripts Python compilam sem erros de sintaxe."""
        py_files = ["run.py"]
        for fname in py_files:
            fpath = os.path.join(PROJECT_DIR, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                code = f.read()
            try:
                ast.parse(code)
            except SyntaxError as e:
                self.fail(f"Erro de sintaxe em {fname}: {e}")

    def test_env_credentials_exist(self):
        """Verifica se as variáveis do .env foram parseadas com sucesso."""
        self.assertIn("UAU_BASE_URL", os.environ)
        self.assertIn("UAU_USUARIO", os.environ)
        self.assertIn("UAU_SENHA", os.environ)
        self.assertIn("UAU_X_INTEGRATION", os.environ)

    def test_frontend_contains_uau_elements(self):
        """Verifica se o index.html contém os elementos da integração ProUAU."""
        target_html = os.path.join(PROJECT_DIR, "index.html")
        self.assertTrue(os.path.exists(target_html), "index.html não encontrado!")
        
        with open(target_html, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("btn-sync-uau", content, "Botão btn-sync-uau não encontrado no HTML.")
        self.assertIn("Sincronizar com ProUAU", content, "Texto 'Sincronizar com ProUAU' não encontrado no HTML.")
        self.assertIn("syncUAU", content, "Função JS syncUAU não encontrada no HTML.")

    def test_sync_uau_database_population(self):
        """Simula a execução da rotina de inserção do sync_uau no banco SQLite com campos ricos."""
        conn = run.get_conn()
        cursor = conn.cursor()

        import datetime
        report_name = f"UAU Sync Test {datetime.datetime.now().isoformat()}"
        report_date = datetime.date.today().isoformat()

        cursor.execute(
            "INSERT INTO reports (report_name, report_date) VALUES (?, ?)",
            (report_name, report_date),
        )
        report_id = cursor.lastrowid

        clients = {
            "CARLOS EDUARDO PROUAU": {
                "cpf_cnpj": "123.456.789-00",
                "cel": "11988887777",
                "email": "carlos.uau@email.com",
                "endereco": "Av. Paulista, 1000",
                "properties": [
                    {
                        "empreendimento": "RESIDENCIAL SUNSET",
                        "identifier": "QUADRA 05 LOTE 12",
                        "venda_id": "887766",
                        "parcels": [
                            {
                                "parcela": "08/60",
                                "vencimento": "15/01",
                                "vencimento_full": "2026-01-15",
                                "valor_original": 1500.00,
                                "valor_juros": 150.00,
                                "valor": 1650.00
                            }
                        ]
                    }
                ]
            }
        }

        run._insert_clients(cursor, report_id, clients)
        conn.commit()

        # Verifica se o relatório foi inserido
        rep = cursor.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        self.assertIsNotNone(rep)

        # Verifica se o cliente foi inserido com os campos ricos
        client = cursor.execute("SELECT * FROM clients WHERE report_id = ?", (report_id,)).fetchone()
        self.assertIsNotNone(client)
        self.assertEqual(client["name"], "CARLOS EDUARDO PROUAU")
        self.assertEqual(client["cpf_cnpj"], "123.456.789-00")
        self.assertEqual(client["endereco"], "Av. Paulista, 1000")

        # Verifica parcela e valores monetários em centavos
        parcel = cursor.execute("SELECT pa.* FROM parcels pa JOIN properties pr ON pa.property_id = pr.id WHERE pr.client_id = ?", (client["id"],)).fetchone()
        self.assertIsNotNone(parcel)
        self.assertEqual(parcel["valor_centavos"], 165000)
        self.assertEqual(parcel["valor_original_centavos"], 150000)
        self.assertEqual(parcel["valor_juros_centavos"], 15000)


if __name__ == "__main__":
    unittest.main()
