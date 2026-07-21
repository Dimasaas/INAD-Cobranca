# HANDOFF — continuidade da revisão de segurança/corretude (INAD v3.2.0)

> **Por que este arquivo existe:** este projeto está no meio de uma revisão de
> segurança e corretude de KPI conduzida com um assistente de IA (Claude
> Code). O responsável pode continuar este trabalho numa outra máquina ou
> numa conversa nova, sem o histórico da sessão anterior — este documento é a
> fonte de verdade sobre o que já foi decidido e o que falta. Leia isto
> inteiro antes de mexer em `run.py`, `inad_template.html`/`inad_whatsapp.html`
> ou nos itens K1-K10/S1-S9 abaixo.
>
> O plano original completo (auditoria com achados detalhados, arquivo/linha
> exatos) existia como `papel-voc-peaceful-catmull.md` num diretório de plans
> local da máquina onde a sessão começou — **não está neste repositório** e
> pode não estar acessível numa máquina nova. Este documento resume tudo que
> importa dessa auditoria; não é preciso ir atrás do arquivo original.

## Contexto do projeto

INAD é um CRM local/intranet de cobrança de inadimplência de uma
construtora, com PII sensível (nomes, telefones, lote/quadra, saldo
devedor), sujeito à LGPD e ao art. 42 do CDC (nota de compliance no topo de
`run.py` — preservar sempre). Arquitetura: servidor HTTP Python puro
(`run.py`, sem framework) + SQLite (`inad_database.db`) + frontend
HTML/JS estático (`inad_template.html` fonte / `inad_whatsapp.html`
compilado via `add_pdf_importer.py`, gitignored) + página separada de
Analytics (`inad_analytics.html` + `analytics.js`).

## Estado atual — o que já foi corrigido e está no `main` (GitHub)

Todos os itens abaixo estão implementados, testados manualmente e/ou via
`tests/test_golden_kpis.py`, commitados e com `git push` feito.

**Segurança (P0):**
- **S8** — XSS armazenado corrigido (`escapeHtml`/`jsAttr` em
  `inad_template.html`, `inad_whatsapp.html`, `analytics.js`).
- **S4+S7** — corpo de POST limitado (`MAX_BODY_BYTES`, 413 se exceder),
  `limit`/`top` com teto (`MAX_RESULT_LIMIT`).
- **S5** — nenhuma resposta HTTP vaza `str(exception)`; detalhe completo só
  em `inad_errors.log` (`_error_response()`).
- **S1+S2+S3** — bind padrão `127.0.0.1` (era `0.0.0.0`); expor na rede exige
  `INAD_HOST=0.0.0.0`/`--host` **e** operador cadastrado (recusa subir sem
  isso). Autenticação por operador (não token único — decisão do
  responsável): tabela `operators`, gerenciada via
  `python run.py --add-operator/--list-operators/--revoke-operator "Nome"`.
  Token só é exigido quando o bind não é loopback (`_is_loopback_bind()`).
  Token chega via header `X-INAD-Token` ou `?token=` na URL (necessário pro
  carregamento inicial da página — um bootstrap no topo de cada HTML captura
  `?token=` uma vez, guarda em `sessionStorage`, injeta em todo `fetch()`
  seguinte). CORS `*` removido (frontend é same-origin com a API).
  **Achado extra corrigido junto:** o fallback de arquivo estático servia
  QUALQUER arquivo da pasta do projeto (inclusive `run.py`, o `.db` inteiro,
  `.git/*`) pra quem pedisse pelo nome — agora só um allowlist explícito de
  assets do frontend é servido (`_STATIC_ALLOWLIST`), sempre, mesmo em bind
  loopback.

**Correção de KPI (P0/P1):**
- **K3** — `reports=` malformado retorna 400 em vez de cair silenciosamente
  para "todos".
- **K8** — datas validadas na importação (ISO ou BR `DD/MM/AAAA`; formato
  desconhecido é recusado com 400) — `_normalize_date()`.
- **K9** — `promises_kept_rate` calculado no backend com guarda contra
  divisão por zero.
- **K5** — constante morta `PREJURIDICO_DAYS` agora é referenciada de fato
  em todos os 4 pontos que decidiam o corte; rótulo "120+" (enganoso, o
  corte real é 121+) virou "121+". **Sem mudança de comportamento/números.**
- **K1** — relatório sem `report_date` agora é **recusado** na importação
  (`POST /api/reports` retorna 400) — decisão do responsável foi exigir a
  data em vez de tratar relatórios sem data como entidades distintas. Frontend
  ajustado para mostrar o erro real (antes mostrava um alerta genérico e
  ainda sobrescrevia a tela como se tivesse salvo com sucesso).
- **K4** — o caminho de `get_kpis_data()` com `report_ids` explícito agora
  usa o mesmo conjunto deduplicado (`active_report_ids`) que o caminho
  default, em vez de ignorar a dedup por `report_date`. Teste golden
  `test_explicit_report_ids_match_default_path` trava isso.
- **D1+D4** — backup JSON restaurável (`backups/`) antes de `DELETE
  /api/reports/<id>` (restaura reenviando o JSON pra `POST /api/reports`).
- **D2** — já estava OK, confirmado (`PRAGMA foreign_keys=ON` por conexão).
- **D5** — parou de "chutar" `+1` (EUA) pra telefone não reconhecido; mostra
  "Telefone a verificar" e não oferece o link.
- **K10** — corrigido off-by-one de fuso nos atalhos de período do
  Analytics (`toLocalISODate()` em vez de `toISOString()`).

**Outros:**
- **A1** — documentado que o servidor é single-thread de propósito
  (`_ReuseServer`), pra não confundir uma futura migração pra
  `ThreadingHTTPServer`.
- **Modo demo removido por completo** (pedido explícito do responsável,
  "buggy e sem aplicação prática") — sem relação com o plano de auditoria
  original, mas fez parte desta sessão. `generate_demo_data.py` apagado,
  `INAD_DEMO`/`--demo`/botão "🧪 Modo Demo"/endpoint `/api/demo/launch`
  removidos de `run.py` e de todo o frontend.
- **S9 — PII removida do código E do histórico do git**: além dos scripts
  já mapeados na auditoria original (`scripts/fix_maria_phone.py`,
  `update_maria_dalva.py`, `update_stefanny.py`, `update_phones_v3.py`,
  `find_alessandra.py`), foram encontrados DURANTE esta sessão (não estavam
  no achado original):
  - uma tabela de 6 nomes+telefones reais **hardcoded na lógica de parsing
    de PDF** (`parseLayoutText()` em `inad_template.html`/`inad_whatsapp.html`)
    — rodava em produção a cada import; removida.
  - `kpi_exclusions.json` (19 nomes reais) estava **rastreado pelo git**;
    foi destravado (`git rm --cached`, adicionado ao `.gitignore` — o
    arquivo continua existindo localmente e `run.py` já tratava sua ausência
    graciosamente).

  O histórico completo do repositório (todos os commits desde o inicial, e
  as 15 tags) foi reescrito com `git-filter-repo` (`python -m
  git_filter_repo`) para remover os 5 scripts + `kpi_exclusions.json` de
  TODOS os commits, e redigir (`--replace-text`) os 6 nomes/telefones em
  todo o histórico de `inad_template.html`. Force-push já feito em `main` e
  em todas as tags. **`inad_whatsapp.html`, `clients_data.json`,
  `pdf_text.txt` nunca foram commitados** (confirmado via `git log --all`) —
  não precisam de reescrita de histórico.
  Repositório é **privado** no GitHub (confirmado via API, 404 sem auth).

**Testes:** `tests/test_golden_kpis.py` — 6 testes, `python -m unittest
discover -s tests -v`. Roda inteiramente em SQLite temporário, nunca toca
`inad_database.db`. Cobre: recovery_rate/dedup/somas com fixture pequena
calculada à mão, validação de data (BR→ISO, formato inválido rejeitado),
K4 (report_ids explícito == caminho default), reconciliação estrutural
(soma dos segmentos novo+antigo == total) sobre um dataset sintético maior
gerado no próprio arquivo de teste (determinístico, `random.Random(42)`,
sem depender de nada externo).

## Decisões já tomadas pelo responsável (não perguntar de novo)

| Item | Decisão |
|---|---|
| §4.1 Modelo de auth (S2) | **Por operador**, não token único compartilhado. |
| §4.3 Dedup (K1) | **Exigir `report_date`** na importação (recusar sem ela) — não "tratar como distintas". |
| §4.4 Normalização de nome (K2) | **Aprovado** — acento/caixa/espaço contam como o mesmo cliente. Abreviações ficam fora do escopo. |
| §4.5 `recovery_rate` (K6) | **Manter como está por enquanto** — não implementar agora. Redefinir isso é uma decisão de negócio maior, tratar em sessão dedicada. |
| §4.6 Fronteira 120/121 (K5) | Já resolvido sem mudar comportamento (ver acima). |
| §4.7 Precisão monetária (K7) | **Migrar agora** para centavos inteiros/Decimal. |
| §4.8 Telefone inválido (D5) | Já resolvido — esconde o link (ver acima). |
| §4.9 Reescrita de histórico git (S9) | Já feita (ver acima). |
| §4.2 CSRF header | Já resolvido — `X-INAD-Token` custom + `?token=` fallback (ver S1-S3 acima). |
| §4.10 Criptografia at-rest (S6) | **Ainda não decidido.** |

## O que falta (nesta ordem sugerida)

### 1. K2 — Normalização de nome  ✅ CONCLUÍDO (branch `feat/plano-kpi-e-correcoes`)

> **Feito:** `normalize_name`/`_normalize_name` aplicados nos dois lados de todas
> as comparações de identidade entre relatórios (exclusões, `recovery_rate`,
> reincidência, `first_seen`/segmentação, worklist, queue, profile, outcomes,
> action_logs). Exibição continua com a grafia original (profile retorna
> `resolved_name`). Teste golden
> `test_name_normalization_treats_spelling_variants_as_same_client` trava o
> comportamento. 7/7 testes passam. Detalhe histórico do plano abaixo.

Já existe em `run.py`:
- `_normalize_name(name)` — remove acentos (via `unicodedata`), colapsa
  espaços, uppercase. Só para comparação, nunca para exibição.
- Registrada como função SQL: `conn.create_function("normalize_name", 1,
  _normalize_name)` dentro de `get_conn()` — ou seja, dá pra usar
  `normalize_name(coluna)` direto em qualquer query SQL.

**Isso ainda NÃO foi aplicado em nenhuma comparação/JOIN/GROUP BY.** Precisa
envolver com `normalize_name(...)` (nos DOIS lados da comparação) em pelo
menos estes pontos (buscar por `c.name`, `client_name`, `GROUP BY.*name`,
`ON fs.name` em `run.py` pra achar todos):

- Toda ocorrência de `... NOT IN (SELECT client_name FROM kpi_exclusions)`
  — há várias: `get_kpis_data`, `get_analytics_data` (dentro da CTE
  `_FIRST_SEEN_CTE` e nas duas queries que a usam), `_client_financials`,
  `get_system_context` (contagem de pré-jurídico).
- `_FIRST_SEEN_CTE` (`GROUP BY c.name`, `JOIN first_seen fs ON fs.name =
  c.name`) — usada em `get_analytics_data` pra segmentação novo/antigo.
- `get_kpis_data()`: `client_sets` é construído em Python a partir de
  `(report_id, name)` — os nomes usados como chave do `set()` (linha com
  `client_sets.setdefault(row[0], set()).add(row[1])`) precisam virar
  `_normalize_name(row[1])` para que `clients_cur - clients_next`
  (recovered) compare identidade normalizada, não string exata.
- `_get_worklist_data()`: `report_clients[r["id"]] = {row[0] for row in
  rows}` (nomes vindos de `clients`) e os `if name not in
  report_clients[...]` — mesma lógica, normalizar as duas pontas.
- `_contact_effectiveness()` e `_calculate_reentries()`: qualquer
  comparação de nome entre `action_logs`/`contact_outcomes` e `clients`.
- Rotas de exclusão (`POST /api/kpis/exclusions`) e outcomes/action_logs:
  decidir se a comparação na hora de marcar "excluído"/"desfecho" também
  deve normalizar contra o que já existe (provavelmente sim, para achar o
  cliente certo mesmo se o operador digitar com grafia diferente da do PDF).

**Depois de aplicar**, adicionar um teste golden que importe o mesmo
cliente com grafias diferentes em dois relatórios (ex.: "JOSÉ DA SILVA" e
"Jose da Silva ") e confirme que conta como o MESMO cliente em
`recovery_rate`/exclusões — isso é o critério de aceitação do item.

⚠️ Cuidado de performance: `normalize_name()` é uma função Python chamada
por linha via SQLite `create_function` — não é indexável. Para o volume de
dados de um CRM local isso é aceitável (correção > performance aqui), mas
não adicionar isso em loops muito grandes sem necessidade.

### 2. K7 — Precisão monetária  ✅ CONCLUÍDO (branch `feat/plano-kpi-e-correcoes`)

> **Feito:** coluna `parcels.valor_centavos INTEGER` adicionada por migração
> idempotente em `init_db` (guarda por `PRAGMA table_info`; `valor` REAL
> mantida por compat/rollback, nunca derrubada). Ingestão grava centavos;
> todas as somas/médias monetárias passaram a usar `valor_centavos` (SUM
> inteiro exato) convertendo pra reais uma única vez — corrigido também o
> double-rounding de `get_analytics_data`. Formato da API preservado (campos
> continuam em reais; `total_owed_cents` é interno, nunca serializado).
> Testes golden `test_exact_cent_sum_avoids_float_drift` e
> `test_valor_centavos_migration_backfill_and_idempotent` (9/9 passam).
> Validado em cópia do banco real: 3482 parcelas, reconciliação exata
> (diff R$ 0,00), migração idempotente. Detalhe histórico do plano abaixo.

**Não iniciado.** Precisa:
- Decidir a representação: coluna `INTEGER` (centavos) é mais simples de
  somar em SQL sem drift do que `Decimal` (que o SQLite não tem tipo nativo,
  exigiria serializar como TEXT). Centavos inteiros é a rota mais direta.
- Migração de schema: `parcels.valor` é hoje `REAL DEFAULT 0.0`
  (`run.py`, `CREATE TABLE parcels` dentro de `init_db()`). Precisa de uma
  migração idempotente (no padrão `PRAGMA table_info` + `ALTER TABLE` já
  usado no arquivo) que converta os valores existentes: `valor_centavos =
  ROUND(valor * 100)`, e decidir se mantém a coluna antiga por
  compatibilidade ou remove.
- Todo lugar que soma/arredonda `valor` precisa ser revisto:
  ingestão (`_insert_clients`, hoje `float(parc.get("valor") or ...)`),
  `_client_financials`, `get_kpis_data`, `get_analytics_data` (o
  double-rounding do achado original está nas linhas que fazem `round(novo[k]
  + antigo[k], 2)` sobre valores JÁ arredondados individualmente).
  Em centavos inteiros, somas ficam exatas (`SUM` de inteiros não tem
  drift); só formatar como reais na apresentação (`valor_centavos / 100`,
  formatado com 2 casas).
- **Isso muda os totais exibidos em centavos** (arredondamentos que hoje
  acumulam erro passam a bater exato) — o responsável já aprovou essa
  variação esperada.
- Adicionar teste golden com N parcelas de valores conhecidos e conferir
  que a soma bate exatamente ao centavo (esse é o critério de aceitação
  original do achado K7).

### 3. K6 — `recovery_rate` (mantido como está por enquanto — não mexer)

Não implementar sem decisão explícita nova. Está documentado como
limitação conhecida no próprio `get_system_context()` (`ai_guidelines`/
`business_rules`). Se retomar isso no futuro, a pergunta em aberto é:
"sumiu do relatório seguinte" continua contando como "recuperado", ou
exigir sinal de pagamento/outcome registrado e reportar "saiu do
relatório" separadamente?

### 4. S6 — Auditoria de acesso / criptografia at-rest (não decidido)

Com a autenticação por operador já existindo (S1-S3), a trilha de auditoria
("quem leu o CPF de qual cliente e quando") ficou tecnicamente viável —
`self.operator_name` já é setado em `do_GET`/`do_POST`/`do_DELETE` depois de
`_authenticate()`, só falta decidir se/como logar isso (ex.: log estruturado
separado para acessos a `/api/clients/profile`). Criptografia at-rest
(SQLCipher) segue sem decisão — impacta o empacotamento PyInstaller
(`INAD_Cobranca.spec`).

## Como verificar depois de qualquer mudança

```
python -c "import ast; ast.parse(open('run.py', encoding='utf-8').read())"   # sintaxe
python -m unittest discover -s tests -v                                      # suíte golden
INAD_PORT=8999 INAD_HEADLESS=1 python run.py                                  # smoke test manual
curl http://127.0.0.1:8999/api/health
```

Sempre rodar a suíte de testes ANTES e DEPOIS de qualquer mudança em K1/K2/
K4/K6/K7 (afetam números de KPI) — é para isso que ela existe.
