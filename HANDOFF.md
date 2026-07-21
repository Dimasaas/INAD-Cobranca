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

**Testes:** `tests/test_golden_kpis.py` — 10 testes, `python -m unittest
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
teste). 13 testes no total.

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
`novos_pre_juridico`. 13 testes no total (`tests/test_golden_kpis.py`).
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
