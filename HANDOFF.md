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
- **K2 — Normalização de nome aplicada** (estava só o esqueleto —
  `normalize_name()` existia mas não era usada em nenhuma comparação). Agora
  todo ponto de identidade de cliente entre relatórios/tabelas normaliza os
  dois lados da comparação: `kpi_exclusions` (todas as ocorrências —
  `_client_financials`, `get_kpis_data`, `get_analytics_data` x2,
  `get_system_context`), `_FIRST_SEEN_CTE` (GROUP BY/JOIN da segmentação
  novo/antigo), `client_sets`/`per_report` (recovery_rate em
  `get_kpis_data` e `get_analytics_data`), `_calculate_reentries`
  (reentradas/timeline — resultado agora é indexado por
  `_normalize_name(name)`, callers em `_get_worklist_data`/`/api/queue`/
  `/api/clients/profile` já ajustados) e `_contact_effectiveness`
  (`report_clients`). O nome de EXIBIÇÃO nunca muda — só a comparação de
  identidade. 3 testes golden novos travam o critério de aceitação (mesmo
  cliente, grafias diferentes, conta como o mesmo em recovery_rate,
  exclusões e reentradas).
  **Gap residual conhecido, fora do escopo desta passada** (não estava no
  checklist original): a detecção de "novos_pre_juridico" em
  `_get_worklist_data()` compara `prev_cf.get(name)` (nome exato do
  relatório anterior) — se a grafia mudar entre os dois relatórios mais
  recentes, essa transição específica pode não ser detectada. Baixo
  impacto (só afeta 1 categoria da worklist, só no boundary exato de 2
  relatórios consecutivos), mas documentado para não ser esquecido.
- **K7 — Precisão monetária migrada para centavos inteiros.** Nova coluna
  `parcels.valor_centavos INTEGER` (migração idempotente automática em
  `init_db()`, backfill via `ROUND(valor*100)` a partir da coluna `valor`
  existente). `valor` (REAL) continua existindo e sendo gravada — é a
  fonte de verdade para exibição do valor de UMA parcela — mas nenhuma
  agregação soma mais `valor` diretamente: `_client_financials`,
  `get_kpis_data`, `get_analytics_data` (série por segmento E transições),
  e o acúmulo por bucket em `/api/clients/profile` somam
  `valor_centavos` (inteiro, soma exata) e só convertem pra reais uma
  única vez ao final via `_cents_to_reais()`. Isso elimina especificamente
  o double-rounding do achado original em `get_analytics_data`
  (`round(novo + antigo, 2)` sobre valores já arredondados individualmente
  — agora a soma novo_centavos+antigo_centavos é uma soma de inteiros,
  exata por construção). Teste golden novo usa valores classicamente
  sujeitos a drift em float puro (0.10+0.20) e confere igualdade EXATA
  (não `assertAlmostEqual`) em cliente/KPI/Analytics. Migração validada
  manualmente num banco simulando o estado pré-K7 (só `valor`, sem
  `valor_centavos`) — backfill correto. Smoke test end-to-end via HTTP
  (import → kpis/summary/queue/profile) confirmado batendo exato ao
  centavo.
- **K6 — `recovery_rate` redefinido (decisão do responsável: opção C,
  "duas métricas lado a lado").** `recovery_rate` ("saiu do relatório
  seguinte") continua existindo **sem nenhuma mudança de comportamento**
  — retrocompatível com a aba KPI e o Analytics. Nova função
  `_confirmed_paid_names(cursor)` retorna o conjunto de identidades
  (normalizadas, K2) com ao menos um desfecho `outcome='pagou'` registrado
  em `contact_outcomes` (sem janela de tempo — qualquer 'pagou' já
  registrado conta). Dois campos novos, aditivos, em cada transição de
  `get_kpis_data()` e `get_analytics_data()`:
  `recovered_confirmed_clients` (contagem) e `recovery_rate_confirmed`
  (%) — o subconjunto de "recovered" que tem 'pagou' confirmado. Nenhum
  campo existente foi removido/renomeado (`GET /api/kpis` e
  `GET /api/kpis/analytics` continuam retrocompatíveis). Teste golden novo
  cobre um cenário com 1 cliente recuperado-sem-confirmação e 1
  recuperado-confirmado, checando que `recovery_rate` não muda e
  `recovery_rate_confirmed` reflete só o confirmado — em ambas as funções.
  Smoke test HTTP confirmado. **Decisão explícita:** não há janela de
  tempo entre o desfecho 'pagou' e a data do relatório onde o cliente
  sumiu — qualquer 'pagou' já registrado pro cliente conta, o que é uma
  simplificação deliberada (ver docstring de `_confirmed_paid_names`).
  **UI atualizada também** — painel principal (`inad_template.html`):
  card "Taxa de Recuperação Média" ganhou um chip azul com a taxa
  confirmada, e o gráfico "Histórico de Recuperação por Período"
  (`drawComparisonChart`) ganhou uma barra fina azul + legenda abaixo do
  título mostrando o subconjunto confirmado ao lado da barra verde
  existente. Analytics (`inad_analytics.html`/`analytics.js`/
  `analytics.css`): tile "Taxa de recuperação" ganhou uma linha `.tile-sub`
  com a taxa confirmada, e o gráfico "Taxa de recuperação por transição"
  ganhou uma 4ª série `Confirmado (pagou)` (cor teal, sempre visível,
  independente do filtro de segmento novo/antigo/todos). Validado
  visualmente com Playwright (screenshot + inspeção do DOM) contra o
  servidor real com dados importados via API — sem erros de console novos
  (os únicos erros de console são fontes do Google bloqueadas no sandbox
  sem internet, pré-existentes, sem relação com a mudança). Lembrar de
  rodar `python3 add_pdf_importer.py` depois de qualquer novo ajuste em
  `inad_template.html` pra regenerar `inad_whatsapp.html`.
- **S6 — decidido e implementado (as duas partes).**
  **(a) Trilha de auditoria:** decisão do responsável — tabela no banco
  (não arquivo de log), consultável via SQL/API. Nova tabela
  `access_audit` (`operator`, `client_name`, `accessed_at`) + índices.
  Novo helper `_log_access(conn, operator, client_name)` chamado de
  dentro de `GET /api/clients/profile` (a única leitura que expõe PII de
  UM cliente específico) — `GET /api/reports/<id>` (leitura em lote, uso
  rotineiro do painel) **não é logado**, de propósito, pra não afogar a
  trilha em ruído. Novo endpoint `GET /api/audit?name=&limit=100`
  (mesmo padrão de `GET /api/outcomes`) pra consultar a trilha. Teste
  golden novo (`test_access_audit_logs_profile_reads`) + smoke test HTTP
  confirmando: 2 consultas de perfil → 2 linhas em `access_audit`; 1
  consulta de relatório em lote → 0 linhas novas.
  **(b) Criptografia at-rest (SQLCipher):** decisão do responsável —
  **não implementar**. Confiar na criptografia de disco do SO
  (FileVault/BitLocker) em vez de adicionar gestão de chave/senha +
  rebuild do empacotamento PyInstaller por plataforma. Nenhuma mudança
  de código para esta parte — só a decisão documentada (não reabrir sem
  decisão explícita nova).

**Outros:**
- **Papel de operador somente-leitura (`can_write`).** Regra nova pedida pelo
  responsável (registrada no `CLAUDE.md`): existe um usuário que só pode
  **ler**, PROIBIDO de editar via API. Antes todos os operadores eram iguais
  (qualquer token válido podia `POST`/`DELETE`). Agora a tabela `operators`
  tem a coluna `can_write INTEGER NOT NULL DEFAULT 1` (migração idempotente
  em `init_db()` no mesmo padrão do K7; bancos legados nascem com `DEFAULT 1`,
  então nenhum operador já cadastrado perde escrita). `_authenticate()` passou
  a retornar a tripla `(ok, operador, pode_escrever)`; `do_POST`/`do_DELETE`
  respondem **403** quando `pode_escrever` é falso (GET segue liberado — o
  operador só-leitura ainda vê tudo). Em bind loopback (uso local do dono da
  máquina) segue sempre com escrita, sem token. CLI:
  `python run.py --add-operator "Nome" --read-only` cria o operador restrito;
  `--list-operators` mostra a coluna de papel (escrita/somente-leitura).
  Validação: teste golden novo
  (`test_read_only_operator_is_authenticated_but_cannot_write` — RO autentica
  mas `pode_escrever=False`, RW e loopback seguem `True`) **+ smoke test HTTP
  8/8** (modo exposto: health público 200; sem token 401; RO → GET 200 /
  POST 403 / DELETE 403; RW passa do portão de escrita) **+ migração de banco
  legado validada** (operador pré-existente preservado com `can_write=1`,
  idempotente). 14 testes no total.
- **A1 — REVISADO: servidor passou a usar pool LIMITADO de threads (padrão
  2, `INAD_MAX_WORKERS`).** A decisão original era single-thread de propósito,
  mas na prática ele **CONGELAVA com navegadores reais**: o Chrome abre
  conexões especulativas (preconnect) que não enviam requisição, e a thread
  única bloqueava lendo uma delas, travando todo o painel (pilha de sockets
  em `CLOSE_WAIT`, `/api/health` sem responder). Correção: `_ReuseServer`
  agora despacha cada conexão para um `ThreadPoolExecutor(max_workers=2)`;
  `INADHandler.timeout` (5s, `INAD_CONN_TIMEOUT`) faz um preconnect ocioso
  soltar o worker em vez de segurá-lo. SQLite sob concorrência já estava
  pronto: `get_conn()` é thread-local (uma conexão por thread) + WAL +
  `busy_timeout=5000` (novo, cobre contenção de escrita entre os 2 workers).
  Validado: suíte golden (14) verde; teste de stress (preconnects ociosos +
  10 requests paralelos) recupera em todos os cenários — 1 preconnect (uso
  real) fluido em ~2s, nunca mais congela; e carga real no Chrome renderiza
  o painel inteiro sem erro de console. **Trade-off do teto de 2 workers:**
  enchente adversarial de sockets ociosos (> nº de workers) ainda causa
  lentidão LIMITADA que se recupera após o timeout — aceitável para um CRM
  local de poucos operadores; suba `INAD_MAX_WORKERS` se precisar de mais.
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

**Testes:** `tests/test_golden_kpis.py` — 14 testes, `python -m unittest
discover -s tests -v`. Roda inteiramente em SQLite temporário, nunca toca
`inad_database.db`. Cobre: recovery_rate/dedup/somas com fixture pequena
calculada à mão, validação de data (BR→ISO, formato inválido rejeitado),
K4 (report_ids explícito == caminho default), reconciliação estrutural
(soma dos segmentos novo+antigo == total) sobre um dataset sintético maior
gerado no próprio arquivo de teste (determinístico, `random.Random(42)`,
sem depender de nada externo), K2 (grafia diferente = mesmo cliente em
recovery_rate/exclusões/reentradas — 3 testes), K7 (soma cent-exata com
valores classicamente sujeitos a drift em float, incluindo o caminho
novo+antigo do Analytics — 1 teste), K6 (recovery_rate inalterado +
recovery_rate_confirmed reflete só quem tem 'pagou' registrado, em
get_kpis_data e get_analytics_data — 1 teste), S6 (`_log_access` grava
e filtra corretamente em `access_audit` — 1 teste) e o gap residual do
K2 em `novos_pre_juridico` (grafia diferente entre os dois relatórios
mais recentes ainda detecta a transição pro corte de 121 dias — 1
teste). 14 testes no total.

## Decisões já tomadas pelo responsável (não perguntar de novo)

| Item | Decisão |
|---|---|
| §4.1 Modelo de auth (S2) | **Por operador**, não token único compartilhado. |
| §4.3 Dedup (K1) | **Exigir `report_date`** na importação (recusar sem ela) — não "tratar como distintas". |
| §4.4 Normalização de nome (K2) | **Aprovado e implementado** — acento/caixa/espaço contam como o mesmo cliente. Abreviações ficam fora do escopo. |
| §4.5 `recovery_rate` (K6) | **Decidido e implementado** — opção C: manter `recovery_rate` como está (retrocompat) e reportar `recovery_rate_confirmed` (baseado em outcome `pagou`) ao lado, sem substituir. |
| §4.6 Fronteira 120/121 (K5) | Já resolvido sem mudar comportamento (ver acima). |
| §4.7 Precisão monetária (K7) | **Migrado** para centavos inteiros (`valor_centavos`). |
| §4.8 Telefone inválido (D5) | Já resolvido — esconde o link (ver acima). |
| §4.9 Reescrita de histórico git (S9) | Já feita (ver acima). |
| §4.2 CSRF header | Já resolvido — `X-INAD-Token` custom + `?token=` fallback (ver S1-S3 acima). |
| §4.10 Auditoria de acesso (S6a) | **Decidido e implementado** — tabela `access_audit` (queryable), não arquivo de log. Loga só `GET /api/clients/profile`. |
| §4.10 Criptografia at-rest (S6b) | **Decidido: não implementar.** Confiar na criptografia de disco do SO (FileVault/BitLocker) em vez de SQLCipher. |
| (extra) Usuário somente-leitura | **Implementado** — coluna `operators.can_write`; operador `--read-only` autentica e faz GET, mas `POST`/`DELETE` retornam 403. |

## O que falta

Não há mais nenhum item do plano original (K1-K10/S1-S9) sem decisão ou
sem implementação.

**Gap residual do K2 — "novos_pre_juridico" (RESOLVIDO em 2026-07-20.)**
`_get_worklist_data()` categorizava a transição pra pré-jurídico comparando
`prev_cf.get(name)` (nome exato do relatório anterior, não normalizado), o
que podia deixar de detectar a transição se a grafia do cliente mudasse
entre os dois relatórios mais recentes. Corrigido normalizando a chave do
lookup (`prev_cf_by_norm`, mesmo padrão usado em `_contact_effectiveness`)
— `name` também é normalizado antes de consultar. Teste golden novo
(`test_name_normalization_applies_to_novos_pre_juridico`) trava o critério:
mesma pessoa, grafia diferente entre os dois relatórios, cruzando o corte
de 121 dias entre um relatório e outro, tem que aparecer em
`novos_pre_juridico`. 14 testes no total (`tests/test_golden_kpis.py`).
Nenhuma outra mudança de comportamento.

Se surgir algo novo, seguir o mesmo padrão: decisão do responsável primeiro
(perguntar as opções, nunca assumir), depois implementação + teste golden +
smoke test manual, e por fim atualizar este arquivo.

## Como verificar depois de qualquer mudança

```
python -c "import ast; ast.parse(open('run.py', encoding='utf-8').read())"   # sintaxe
python -m unittest discover -s tests -v                                      # suíte golden
INAD_PORT=8999 INAD_HEADLESS=1 python run.py                                  # smoke test manual
curl http://127.0.0.1:8999/api/health
```

Sempre rodar a suíte de testes ANTES e DEPOIS de qualquer mudança em K1/K2/
K4/K6/K7 (afetam números de KPI) — é para isso que ela existe.

---

# UAU — Integração para alimentar dados reais (PLANEJADO / em implementação)

> **Contexto (sessão 2026-07-21):** o banco `inad_database.db` desta máquina
> Windows estava **vazio** (schema criado, 0 linhas em todas as tabelas). O
> único caminho de entrada de dados implementado hoje é importar PDF pelo
> frontend → `POST /api/reports`. O `.env` tem credenciais da **UAU** (ERP da
> construtora, Senior Cloud) que **nunca foram usadas** — não existe uma linha
> sequer de código de integração. O responsável decidiu **construir a
> integração UAU** para popular o banco com dados reais, com sync **agendado**,
> para **uma empresa específica**, e — ponto central — o relatório precisa
> **reconstruir o histórico** para os Analytics fazerem sentido desde o dia 1
> (recovery_rate, segmentação novo/antigo, reentradas e timeline são todos
> longitudinais: comparam relatórios consecutivos; num banco zerado só com sync
> diário para frente, ficariam vazios por semanas). Esta seção é o plano
> durável — implementar seguindo o padrão do projeto (decisão → código → teste
> golden → smoke test → atualizar este arquivo).

## O que JÁ foi validado ao vivo nesta máquina (não repetir a descoberta)

- **Endpoint alcançável e autenticação FUNCIONA.** `UAU_BASE_URL` no `.env` =
  `https://gamma-api.seniorcloud.com.br:51910/uauAPI` (ambiente **gamma** =
  provável homologação — **o responsável ainda vai confirmar se os dados de
  gamma são reais ou de teste**; se precisar de produção, ele troca a URL/
  credenciais no `.env`).
- **Fluxo de auth:** `POST {base}/api/v1.0/Autenticador/AutenticarUsuario`,
  corpo `{"Login": UAU_USUARIO, "Senha": UAU_SENHA, "UsuarioUAUSite": ""}`,
  header **`X-INTEGRATION-Authorization`: `UAU_X_INTEGRATION`** (ATENÇÃO: o
  header é `X-INTEGRATION-Authorization`, NÃO `X-INTEGRATION` — usar o nome
  errado dá HTTP 500 null-ref em `AutenticadorHandler`). Resposta = um **token
  JWT** (string). Esse token vai no header **`Authorization`** de TODA chamada
  seguinte, junto com o `X-INTEGRATION-Authorization` (os dois em todo POST).
  TLS do host gamma não valida cadeia limpa → usar contexto sem verificação
  **apenas** para gamma; revisar ao ir para produção.
- **Empresas ativas em gamma:** `POST .../api/v1.0/Empresa/ObterEmpresasAtivas`
  (sem corpo) retornou **7 empresas, códigos 1–7**. Nome de cada uma está no
  campo `Desc_emp` (a listagem também traz `CGC_emp`=CNPJ, `Endereco_emp`,
  `Fone_emp`). **Falta o responsável escolher o código da empresa-alvo.**
- **Swagger completo** baixado (v1.0, 438 endpoints). Se precisar de novo:
  `GET {base}/1.0/swagger` (com o header `X-INTEGRATION-Authorization`); a UI
  fica em `{base}/swagger/ui/index`.
- **Versão no path:** usar `version = 1` → `/api/v1/...` (a doc chama de v1.0).

## Cadeia de ingestão (mapeada do Swagger — endpoint principal + enriquecimento)

Todos POST; headers `Authorization` + `X-INTEGRATION-Authorization` em todos.

1. **`Venda/ConsultarContasReceberCalc`** ← ENDPOINT PRINCIPAL (consulta em
   massa). Corpo: `{ "Vendas": [{ "Empresa": <cod>, "Obra": <cod_obra opc> }],
   "DataFim": "<hoje/data-alvo>" }` (`DataInicio`, `TiposParcela`, `Numero` da
   venda são opcionais; só `Empresa` é obrigatório no item de `Vendas`).
   Retorna **array de parcelas em aberto** com: `empresa`, `obra`,
   `numeroVenda`, `numeroParcela`, `tipoParcela`, **`cliente`** (código da
   pessoa), **`nomeCliente`**, **`dataVencimento`**, `valorReajustado`,
   `valorPrincipal`, `valorJuros`, **`valorJurosAtraso`**, **`valorMulta`**,
   **`valorCorrecaoAtraso`**, etc. **Em atraso = `dataVencimento < hoje`**
   (não há flag booleano; encargos `valorJurosAtraso/valorMulta > 0`
   corroboram). Agrupar por `cliente` e `numeroVenda`.
   - **Incerteza a validar em runtime:** o Swagger marca só `Empresa` como
     obrigatório, sugerindo que aceita filtro empresa-only e varre todas as
     vendas. Se na prática exigir `Numero`, enumerar as vendas antes com
     `Venda/RetornaChavesVendasPorPeriodo` (corpo: `data_inicio`, `data_fim`,
     `statusVenda="0"` normal, `listaEmpresaObra:[{codigoEmpresa,codigoObra}]`;
     resposta é **string JSON serializada**, parsear na mão).
2. **Enriquecimento por código de `cliente` distinto** (o `ContasReceberCalc`
   NÃO traz CPF/telefone/unidade):
   - `Pessoas/ConsultarPessoasPorCondicao` → CPF/CNPJ, e-mail. Corpo:
     `condicaoConsultarPessoa` = WHERE SQL (ex.: `cod_pes IN (1,2,3)` em lote).
     Resposta tipada: `CodigoPessoa`, `NomePessoa`, `CpfPessoa`, endereços.
   - `Pessoas/ConsultarTelefones` → celular. Corpo `{ "Numero": <cod_pessoa> }`.
     Resposta: `DDD`+`Telefone`, `Tipo` (**2 = Celular**), `Principal`. Usar
     `Tipo==2`.
   - `Pessoas/ConsultarUnidades` → identificação lote/quadra/unidade. Corpo
     `{ "CodigoPessoa": <cod> }` (ou `CpfCnpj`). Resposta: `Venda`, `Obra`,
     `Empresa`, `Produto`, **`Identificador`** (unidades separadas por " | ").
     Casa `Venda/Obra/Empresa` com o `numeroVenda` de (1).
   - **Alternativa por CPF (drill-down de UM devedor, tela de detalhe):**
     `Recebiveis/ParcelasECobrancasDoCliente` (corpo `Cpf`, `ValorReajustado`)
     — resposta totalmente tipada com vendas+unidades+parcelas+boletos+pix num
     payload só. Não faz varredura em massa; ótimo para o perfil do cliente.

## Mapeamento UAU → schema INAD (reusar `_insert_clients`, sem inventar schema)

Montar o mesmo dict que `POST /api/reports` já consome e chamar `_insert_clients`
(ver `run.py`), de modo que **todo o pipeline de KPI/Analytics funcione sem
mudança**. Estrutura: `{ report_name, report_date, clients: { <nome>: {
cpf_cnpj, cel, email, properties: [ { venda_id, identifier, parcels: [
{ parcela, vencimento, vencimento_full (ISO), valor } ] } ] } } }`.
- `clients[nome]` ← `nomeCliente` (exibição; a identidade usa `normalize_name`).
- `cpf_cnpj` ← `CpfPessoa`; `cel` ← DDD+Telefone (Tipo 2); `email` ← `EmailPessoa`.
- `properties[].venda_id` ← `numeroVenda`; `identifier` ← `Identificador`.
- `parcels[].vencimento_full` ← `dataVencimento` (normalizar p/ ISO via
  `_normalize_date`); `valor` ← usar o valor devido da parcela (definir com o
  responsável qual: `valorReajustado` = com correção/encargos, ou
  `valorPrincipal`). **K7:** `valor_centavos` é derivado automaticamente em
  `_insert_clients` (`round(valor*100)`) — manter.
- **PII/LGPD:** os dados trazidos são PII sensível (nome, CPF, telefone,
  unidade, saldo). Não logar em claro; a trilha `access_audit` (S6) e a
  criptografia de disco do SO (S6b) continuam sendo a política. Nunca commitar
  o `.db` nem dumps.

## O CENTRAL: reconstrução do histórico para os Analytics (backfill)

Problema: os Analytics comparam `reports` consecutivos ordenados por
`report_date`. Um único snapshot de hoje não produz recovery_rate, novo/antigo,
reentradas nem timeline. `ConsultarContasReceberCalc` calcula **como está
HOJE** (não aceita "data de cálculo retroativa" no request — só filtra por
vencimento), então chamá-lo hoje não devolve o estado passado da carteira.

**Solução planejada — reconstruir snapshots mensais a partir do razão de
parcelas + datas de pagamento:**
1. Para a empresa-alvo, obter, por venda/cliente, TODAS as parcelas (número,
   vencimento, valor) **e os pagamentos** (data de quitação de cada parcela).
   Fontes candidatas de pagamento (validar qual traz data de pagamento
   confiável em runtime): `Venda/BuscarParcelasRecebidas`,
   `Venda/BuscarRecebimentosDaVenda`,
   `ExtratoDoCliente/ConsultarDadosDemonstrativoPagtoCliente` ("parcelas
   pagas"), ou o razão do `Recebiveis/ParcelasECobrancasDoCliente`.
2. Definir uma janela (ex.: **últimos 12 meses**). Para cada data de corte
   mensal `S` (ex.: último dia de cada mês), um cliente está inadimplente em
   `S` se tem ao menos uma parcela com `vencimento <= S` E
   `(não paga OU data_pagamento > S)`. Isso reconstrói exatamente "quem estava
   em atraso naquele mês".
3. Inserir **um `report` por mês** (`report_date = S`), com os clientes/parcelas
   em atraso naquele corte, via `_insert_clients`. Resultado: recovery_rate
   (saiu do relatório do mês seguinte = pagou entre um corte e outro), timeline,
   reentradas e segmentação novo/antigo passam a refletir o **histórico real**
   desde o primeiro uso. O sync agendado apenas acrescenta o snapshot novo por
   cima dessa base.
   - **Risco/incerteza a resolver na implementação:** confirmar que as datas de
     pagamento vêm confiáveis por parcela (senão o "recuperado histórico" fica
     impreciso). Se a data de pagamento não for obtível de forma confiável,
     fallback MVP: gerar os snapshots mensais só do estado atual replicado NÃO
     serve (falsearia recuperação); nesse caso reduzir a ambição para "histórico
     a partir de hoje" e deixar claro na UI que o histórico começa na data de
     ativação. **Decidir com o responsável se o backfill sai ou não conforme a
     qualidade das datas de pagamento.**

## Design do sync AGENDADO (decisão do responsável: agendado)

- Implementar como **função/CLI** primeiro (`python run.py --sync-uau
  --empresa <cod> [--backfill-meses 12]`) — testável, fora do alcance do
  operador somente-leitura (o sync ESCREVE; nunca expor ao RO). A CLI:
  autentica → (na 1ª vez) roda o backfill histórico → insere/atualiza o
  snapshot do dia → `data_version` sobe e o frontend já reflete.
- **Agendamento (Windows):** Task Scheduler (`schtasks`) chamando a mesma CLI
  1x/dia (ex.: de manhã). Documentar o comando no `TUTORIAL_INTRANET_WINDOWS.md`.
  A máquina precisa estar ligada no horário. (No Mac seria `launchd`/cron, mas
  só a máquina Windows alcança a UAU — ver memória do projeto.)
- **Idempotência:** um sync do mesmo dia não deve duplicar o report daquele
  `report_date`; reusar a dedup por `report_date` que já existe
  (`active_report_ids`) — se já existe report para a data, substituir/pular
  (definir política; provavelmente substituir pelo mais recente do dia).
- **Credenciais:** só no `.env` (já gitignored). Token JWT vive em memória
  durante a execução; não persistir em disco.

## Estado das decisões do responsável (sessão 2026-07-21)

| Item | Decisão |
|---|---|
| Fonte dos dados reais | **Construir integração UAU** (não import de PDF/JSON). |
| Ambiente | **A confirmar** — `.env` aponta pra gamma; responsável vai dizer se é real ou se troca pra produção. |
| Escopo | **Uma empresa específica** — responsável vai escolher o código (empresas 1–7 ativas em gamma). |
| Gatilho | **Agendado** (via Task Scheduler no Windows), implementado sobre uma CLI `--sync-uau`. |
| Analytics | **Reconstruir histórico** (backfill de snapshots mensais) para as métricas longitudinais valerem desde o dia 1. |

## Próximos passos concretos (ordem sugerida)

1. Responsável confirma **ambiente** (gamma×produção) e **código da empresa**.
2. Escrever um módulo cliente UAU em `run.py` (ou `uau_client.py` novo, sem
   dependências externas — só `urllib`/`ssl` da stdlib, como já validado):
   `_uau_auth()`, `_uau_post(path, payload, token)`, e as funções da cadeia.
3. Implementar `sync_uau(empresa, backfill_meses)` que monta o dict e chama
   `_insert_clients`; validar a incerteza do `ConsultarContasReceberCalc`
   (empresa-only vs precisa enumerar vendas) e a qualidade das datas de
   pagamento para o backfill.
4. CLI `--sync-uau` + guarda para NUNCA rodar em contexto de operador RO.
5. Teste golden com **payload UAU fake** (fixture determinística, nunca a API
   real) validando o mapeamento UAU→INAD e a reconstrução de snapshots mensais.
6. Smoke test end-to-end contra gamma (1 empresa, janela curta) conferindo que
   KPI/Analytics renderizam com o histórico.
7. Documentar o agendamento no `TUTORIAL_INTRANET_WINDOWS.md` e atualizar este
   HANDOFF marcando o que saiu.

**Nada disso está implementado ainda** — só a descoberta/validação de auth e a
listagem de empresas foram feitas. O arquivo do Swagger baixado fica no
scratchpad da sessão (temporário); rebaixar com o GET acima se precisar.
