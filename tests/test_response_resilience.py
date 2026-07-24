"""
Testes de resiliência de socket: quando o cliente (navegador/curl) desconecta
antes de a resposta ser escrita, o servidor NÃO deve tratar isso como erro 500
nem logar traceback — é desconexão do cliente, não falha de servidor.

Cobre a blindagem de _json_response / _error_response em run.py.
"""

import os
import sys
import logging
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import run


class _FakeWFile:
    """wfile falso: por padrão registra os bytes escritos; se `raise_on_write`
    for setado, levanta esse erro (simula socket fechado pelo cliente)."""

    def __init__(self, raise_on_write=None):
        self.raise_on_write = raise_on_write
        self.written = b""
        self.write_calls = 0

    def write(self, data):
        self.write_calls += 1
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.written += data


class _FakeHandler:
    """Handler HTTP falso com a superfície mínima usada por _json_response /
    _error_response: send_response/send_header/end_headers como no-ops, além de
    command/path e um wfile configurável."""

    def __init__(self, wfile):
        self.command = "POST"
        self.path = "/api/sync_uau"
        self.wfile = wfile

    def send_response(self, status):
        pass

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


class ResponseResilienceTests(unittest.TestCase):

    def test_json_response_swallows_client_disconnect(self):
        """Cliente fecha o socket → _json_response NÃO deve levantar exceção."""
        for exc_cls in (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            with self.subTest(exc=exc_cls.__name__):
                handler = _FakeHandler(_FakeWFile(raise_on_write=exc_cls()))
                try:
                    run._json_response(handler, {"status": "debug"})
                except Exception as exc:  # noqa: BLE001 — o teste é justamente garantir que não sobe
                    self.fail(f"_json_response propagou {type(exc).__name__} numa desconexão")

    def test_json_response_writes_body_on_happy_path(self):
        """Caminho feliz preservado: com socket normal, o corpo é escrito."""
        handler = _FakeHandler(_FakeWFile())
        run._json_response(handler, {"status": "ok"})
        self.assertIn(b'"status"', handler.wfile.written)
        self.assertEqual(handler.wfile.write_calls, 1)

    def test_error_response_ignores_disconnect_without_write_or_traceback(self):
        """_error_response com erro de desconexão: loga INFO, NÃO escreve
        resposta (socket morto) e NÃO loga traceback (nível ERROR)."""
        handler = _FakeHandler(_FakeWFile())
        with self.assertLogs(level=logging.INFO) as captured:
            run._error_response(handler, ConnectionAbortedError())
        # Nenhuma tentativa de escrever resposta no socket já fechado.
        self.assertEqual(handler.wfile.write_calls, 0)
        # Nenhum log de nível ERROR (traceback) para desconexão.
        self.assertFalse(
            any(rec.levelno >= logging.ERROR for rec in captured.records),
            "desconexão do cliente não deve gerar log de ERROR/traceback",
        )

    def test_error_response_still_reports_real_errors(self):
        """Erro real (não desconexão): _error_response escreve a resposta
        genérica e loga em nível ERROR."""
        handler = _FakeHandler(_FakeWFile())
        with self.assertLogs(level=logging.ERROR):
            run._error_response(handler, ValueError("falha real"), 500)
        self.assertIn(b'"error"', handler.wfile.written)


if __name__ == "__main__":
    unittest.main()
