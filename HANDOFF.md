# HANDOFF — INAD (v3.2.0+)

> **Status do Projeto:** Todas as revisões de segurança (S1–S9) e corretude de KPIs (K1–K10) foram concluídas, testadas e sincronizadas com a branch `main`.

---

## 🟢 Estado Atual (Tudo Concluído e Testado)

Todos os itens da auditoria foram implementados e validados pela suíte de testes automáticos (`tests/test_golden_kpis.py`):

1. **Segurança (S1–S9):**
   - Bind em localhost por padrão (`127.0.0.1`).
   - Autenticação obrigatória por operador quando exposto em rede (`INAD_HOST=0.0.0.0`).
   - Papel de operador somente-leitura (`can_write=0` / `--read-only`).
   - Sanitização de XSS (stored XSS).
   - Trilha de auditoria em banco de dados (`access_audit`) para consultas de perfil individual (PII).
   - PII removida do histórico do Git e do código-fonte.

2. **KPIs e Métricas (K1–K10):**
   - Normalização de identidade de clientes (`normalize_name` sem acentos/caixa/espaços extras).
   - Precisão monetária exata em centavos inteiros (`parcels.valor_centavos`).
   - Relatórios sem `report_date` recusados no upload (exigência de data).
   - Taxa de recuperação confirmada (`recovery_rate_confirmed` com base em desfecho `pagou`) reportada ao lado de `recovery_rate`.
   - Worklist e reentradas ajustadas com normalização de nome.

---

## 📌 Próximas Tarefas / Fila de Trabalho

*(Adicione novos itens de roadmap ou solicitações pendentes nesta seção)*

- [ ] Definir novos requisitos para próximas versões.

---

## 🧪 Como Verificar

Sempre execute a suíte de testes antes e depois de realizar qualquer alteração:

```bash
# Executar a suíte de testes golden (14 testes)
python -m unittest discover -s tests -v

# Verificar sintaxe do servidor
python -c "import ast; ast.parse(open('run.py', encoding='utf-8').read())"
```
