# PLANO — INAD v4 (reescrita em `inad-v4/`)

> **Status: planejamento CONCLUÍDO, implementação NÃO iniciada.**
> Este documento é a especificação aprovada pelo responsável para a reescrita
> da ferramenta. A implementação deve segui-lo por inteiro, numa passada só
> (decisão do responsável), e atualizar este arquivo se algo divergir na prática.
> Contexto da v3: ver `../AI_CONTEXT.md` e `../HANDOFF.md`.

Mesmas finalidades da v3 (importar PDF de inadimplência → CRM de cobrança via
WhatsApp + análise de recuperação), com o modelo de dados corrigido na raiz.
Servidor **interno, sem autenticação**. Score **persistido por cliente**.

---

## Decisões do responsável (fechadas — não reperguntar)

| Tema | Decisão |
|---|---|
| Autenticação | **Removida por completo** — sem tokens/operadores/gate de rede. Bind `0.0.0.0:8000` direto (LAN interna confiável). |
| Convivência | v4 nasce em `inad-v4/` isolada; **a v3 na raiz permanece intacta** e é a release oficial (v3.3.0) até decisão de migrar. |
| Escopo do build | **Projeto inteiro numa passada** (backend + frontend + testes + migração + PR draft), sem parar no meio. |
| Score | Mesma fórmula transparente da v3 (45/35/20, P90), mas **persistido em `clientes.score`** e exibido em todo card. Pesos em `config`. |
| Parser de telefone | Manter a lógica de correção, **generalizando** as 2 linhas que embutem números reais (`startsWith("3519")` e o literal `44973772012`) numa heurística genérica de DDD deslocado em números de 12 dígitos. **Nunca recommitar PII** (desfaria o purge S9). |
| Visual | Tema **claro + escuro** (viewer escolhe), acabamento **polido e funcional** — ver seção "Direção visual". |

## Princípios (o que muda vs v3)

1. **Cliente é entidade canônica**, identificada por CPF/CNPJ; nome é só display.
2. **Estado de cada cliente-período é explícito** (ledger `saidas`), não inferido por diff.
3. **Dinheiro em centavos inteiros** de ponta a ponta — nenhuma coluna REAL de dinheiro,
   nenhum `float` no caminho (string BR → `Decimal` → int na ingestão).
4. **Uma única fonte da série de relatórios**; `relatorios.data` UNIQUE (o conceito de
   `is_duplicate` deixa de existir; reimportar a mesma data substitui o snapshot).
5. **Sem passo de compilação** de frontend (`add_pdf_importer.py` não existe na v4).
6. Fix PyInstaller onefile (`sys.frozen` → dirname de `sys.executable`) desde o dia 1.

Com isso, ~70% dos bugs da auditoria K1–K10 da v3 ficam *inexpressáveis* por construção.

---

## Estrutura de arquivos

```
inad-v4/
  run.py            # entrypoint: CLI + boot (porta/host/headless), fix sys.frozen
  inad/
    __init__.py
    util.py         # normalize_name, normalize_cpf, parse_money_br→cents, datas, cents→BRL
    db.py           # conexão, DDL, migrações idempotentes, serie_efetiva()
    ingest.py       # matching em cascata, gravação de snapshot, recompute ledger + score
    metrics.py      # score, evolução, transições, matriz aging, safras, fila, worklist, perfil
    server.py       # HTTP handler sem auth, JSON, allowlist de estáticos
  static/
    index.html      # SPA único (abas), CSS embutido, dois temas
  libs/             # chart.umd.min.js, pdf.min.js, pdf.worker.min.js (copiados da v3)
  tests/
    test_v4.py      # suíte golden (ver seção Testes)
  migrar_v3.py      # migração de um inad_database.db v3 → schema v4
  PLANO.md          # este arquivo
  README.md         # criado na entrega
```

## Schema (SQLite, WAL)

```sql
clientes(id PK, cpf_cnpj TEXT UNIQUE,          -- NULL permitido (PDF sem CPF)
         nome_exibicao TEXT NOT NULL,
         score INTEGER NOT NULL DEFAULT 0,
         score_componentes TEXT DEFAULT '{}',  -- json {valor, aging, reincidencia}
         atualizado_em TIMESTAMP);
cliente_aliases(cliente_id FK, nome_normalizado TEXT UNIQUE);
relatorios(id PK, nome TEXT NOT NULL, data DATE NOT NULL UNIQUE, importado_em TIMESTAMP);
entradas(id PK, relatorio_id FK CASCADE, cliente_id FK,
         cel TEXT DEFAULT '', email TEXT DEFAULT '',
         UNIQUE(relatorio_id, cliente_id));
imoveis(id PK, entrada_id FK CASCADE, venda_id TEXT, identificador TEXT);
parcelas(id PK, imovel_id FK CASCADE, parcela TEXT,
         vencimento_full DATE NOT NULL, valor_centavos INTEGER NOT NULL DEFAULT 0);
saidas(cliente_id FK, relatorio_id FK, status TEXT NOT NULL,
       valor_centavos INTEGER NOT NULL DEFAULT 0,   -- dívida no momento da saída
       manual INTEGER NOT NULL DEFAULT 0,           -- 1 = resolução definida à mão
       definido_em TIMESTAMP, UNIQUE(cliente_id, relatorio_id));
  -- status: inadimplente | saiu_nao_confirmado | saiu_pago_confirmado
  --         | encaminhado_juridico | excluido_kpi
contatos(id PK, cliente_id FK, venda_id TEXT DEFAULT '', enviado_em TIMESTAMP);
desfechos(id PK, cliente_id FK, tipo TEXT NOT NULL,  -- mesmos 7 tipos da v3
          promessa_data DATE, proximo_contato DATE, nota TEXT DEFAULT '',
          criado_em TIMESTAMP);
exclusoes_kpi(cliente_id INTEGER PRIMARY KEY);
config(chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
-- seeds de config: peso_valor=45, peso_aging=35, peso_reincidencia=20, prejuridico_dias=120
```

Buckets de aging fixos: 0-30, 31-60, 61-90, 91-120, 121+ (como na v3).
Estágios: lembrete (≤30), firme (31-90), serio (91-120), pre_juridico (>120).

## Ingestão (`ingest.py`)

- **Formato de entrada = árvore JSON idêntica à que o parser client-side v3 produz**
  (`{nome:{cpf_cnpj,cel,email,properties:[{venda_id,identifier,parcels:[{parcela,
  vencimento_full,valor}]}]}}`) — compatível com backups da v3 e com o parser portado.
  `valor` aceita número OU string BR ("1.234,56"); sempre convertido via `Decimal`.
- **Cascata de matching por cliente:** (1) `normalize_cpf` bate → mesmo cliente;
  (2) senão `normalize_name` bate um alias → mesmo cliente (e grava o CPF se veio agora);
  (3) senão → cliente novo. Sempre registra o alias novo e atualiza `nome_exibicao`.
- `data` UNIQUE: reimportar a mesma data **substitui** o snapshot daquela data
  (DELETE + INSERT na mesma transação — idempotente).
- Pós-import, na mesma transação: **recompute do ledger** (para cada transição
  consecutiva da série, cliente presente→ausente vira `saiu_nao_confirmado` com o valor
  devido no momento; linhas com `manual=1` nunca são sobrescritas; desfecho `pagou`
  existente promove a `saiu_pago_confirmado`; reaparição marca a reentrada para fins de
  reincidência) e **recálculo do score persistido** de todos os clientes do último
  relatório (demais clientes: score congelado da última presença).

## Score (`metrics.calcular_score`, persistido)

`score = peso_valor*min(devido/P90,1) + peso_aging*min(dias_atraso/180,1)
       + peso_reincidencia*min(reentradas/3,1)` — pesos lidos de `config`;
P90 = percentil 90 do total devido no relatório mais recente; componentes gravados
em `clientes.score_componentes` para exibição explicável no card.

## API (sem auth — todas as rotas abertas na LAN)

| Método/rota | Descrição |
|---|---|
| `GET /api/health` | status/porta/versão |
| `GET /api/relatorios` | série efetiva (id, nome, data) |
| `POST /api/relatorios` | importa árvore JSON `{nome, data, clientes}`; mesma data substitui |
| `DELETE /api/relatorios/<id>` | remove snapshot (com backup JSON em `backups/`, como na v3) |
| `GET /api/clientes` | lista canônica com score/componentes/estágio/última presença |
| `GET /api/cliente?id=\|cpf=\|nome=` | perfil completo: dívidas, timeline, contatos, desfechos, score |
| `GET /api/fila` | fila priorizada por score (?estagio&min_dias&limite) |
| `GET /api/worklist` | 4 categorias (promessas vencidas, recontato, sem resposta, novos pré-jurídico) |
| `GET /api/resumo` | tiles executivos (totais, delta vs anterior, aging, top devedores) |
| `GET /api/analytics` | evolução + transições (contagem E valor; "saiu" vs "confirmado", direto do ledger) |
| `GET /api/analytics/aging` | matriz de migração de buckets entre relatórios consecutivos |
| `GET /api/analytics/safras` | coortes por mês de estreia × % ainda presente |
| `POST /api/contatos` | registra disparo `{cliente_id, venda_id?}` |
| `POST /api/desfechos` | registra desfecho `{cliente_id, tipo, promessa_data?, proximo_contato?, nota?}` |
| `POST /api/saidas` | reclassificação manual `{cliente_id, relatorio_id, status}` → `manual=1` |
| `GET/POST /api/exclusoes` | exclusões de KPI por `cliente_id` |
| `GET /api/config` / `POST` | pesos do score e `prejuridico_dias` |

Regras de servidor: allowlist de estáticos (SPA + libs, nada além), redirects
relativos (sem hostname fixo), corpo POST limitado (20 MB), respostas JSON UTF-8.

## Frontend (`static/index.html`, SPA único)

Abas: **Fila** (cards com score badge + estágio + link WhatsApp + registrar desfecho),
**Worklist**, **KPIs** (evolução, transições com as duas taxas), **Analytics**
(matriz de aging, safras, recuperação por valor), **Importar** (drag&drop PDF +
colar/enviar JSON, com **preview e confirmação** antes de gravar), **Clientes**
(lista canônica com busca por nome/CPF/telefone).

- Parser de PDF portado do template v3 (`extractTextFromPDF` + `parseLayoutText` +
  `extractReportDate`), com a generalização de telefone decidida acima; `valor` de
  parcela mantido como **string BR** até o servidor (sem float no JS).
- WhatsApp: portar `gender/greeting/tc/waLink` (validação de DDD BR) como estão.
  **Mensagens por estágio** (4 templates: lembrete, firme, serio, pre_juridico),
  todas factuais e respeitosas conforme a nota CDC art. 42 da v3 — pré-jurídico
  informa que o caso "poderá ser encaminhado ao setor jurídico", **nunca** ameaça
  processo/negativação/perda do imóvel. Textos finais escritos na implementação
  seguindo essas restrições.

### Direção visual (adendo aprovado: "deixar mais bonito")

Um CRM operado o dia todo → **design de informação**, não enfeite. Evitar
explicitamente os tiques da v3 (gradiente roxo→azul, glassmorphism, acento chapado).

- **Claro + escuro, viewer escolhe** — `prefers-color-scheme` + toggle manual
  (`data-theme` no `:root` vencendo a media query nos dois sentidos); os dois temas
  com o mesmo cuidado, sem inversão ingênua.
- **Neutro escolhido** (cinza com leve viés do acento), fundo claro real.
- **Um acento só** (azul-petróleo/teal). **Cor semântica de risco separada do acento**:
  verde (<40) / âmbar (40-69) / vermelho (≥70 e pré-jurídico) — só para estado
  (badge de score, faixa na borda do card, chip de estágio), nunca decorativa.
- **Tipografia com hierarquia real**: display com peso, texto legível, mono para
  números (`font-variant-numeric: tabular-nums` em toda coluna de dinheiro/score).
- **Densidade sem cansaço**: resumo antes do detalhe (tiles → fila/tabela), zebra
  sutil, números alinhados à direita, gráficos com área leve/grid discreto/ponto
  final destacado.
- Layout por grid/gap; tabelas e gráficos largos com `overflow-x:auto` no próprio
  container; foco de teclado visível; `prefers-reduced-motion` respeitado;
  transições curtas só onde ajudam. **Sem dashboard-vitrine.**

## Testes (`tests/test_v4.py`) — golden desde o dia 1

- Identidade: mesmo CPF com nomes diferentes = 1 cliente; sem CPF cai no nome
  normalizado; alias novo gravado; CPF chegando depois "solda" o cliente.
- Dinheiro: centavos exatos (0.10+0.20 e afins), igualdade EXATA, string BR parseada.
- Série: `data` UNIQUE substitui snapshot; nunca dois relatórios na mesma data.
- Ledger: presente→ausente gera `saiu_nao_confirmado` com valor; desfecho `pagou`
  promove; reclassificação `manual=1` sobrevive a recompute; reaparição conta
  reincidência.
- Score: persistido bate com recálculo; componentes coerentes; pesos de config
  respeitados.
- Reconciliação: soma por cliente == total do relatório; matriz de aging e safra
  com fixture calculada à mão.
- Migração: fixture com schema v3 sintetizado → `migrar_v3.py` → contagens e
  totais batem no v4.

## Migração (`migrar_v3.py`)

Lê um `inad_database.db` v3 (caminho por argumento), reproduz os relatórios na ordem
de data efetiva (dedup v3: maior id por data) através do `ingest` v4, e carrega
`action_logs`→`contatos`, `contact_outcomes`→`desfechos`, `kpi_exclusions`→
`exclusoes_kpi`, casando por CPF e nome normalizado. Idempotente (re-rodar não duplica).

## Fases de execução e critérios de aceite (tarefas #15–20)

| Fase | Entrega | Aceite |
|---|---|---|
| A (#15) | Esqueleto + libs copiadas | servidor sobe headless, `/api/health` ok, suíte vazia roda |
| B (#16) | db + ingest | testes de identidade/centavos/UNIQUE verdes |
| C (#17) | metrics + score persistido | testes de ledger/score/reconciliação/aging/safra verdes |
| D (#18) | server + SPA dois temas | smoke HTTP em todas as rotas; screenshot Playwright da Fila com badges nos DOIS temas |
| E (#19) | suíte completa + migração | 100% verde; migração da fixture v3 validada |
| F (#20) | README + commit + push + **PR draft** | diff revisado; nada de PII; suíte verde no estado final |

## Definição de pronto

Suíte 100% verde; smoke E2E via HTTP real; screenshots dos dois temas conferidos;
**zero código de autenticação**; score visível em todo card; migração validada;
PR draft aberto no branch designado. `python run.py` numa máquina limpa sobe e
funciona sem nenhuma dependência além da stdlib.

## Riscos e pendências conscientes

- **Fidelidade do parser de PDF**: sem PDFs reais nesta sessão, o parser portado é
  validado com fixture de texto sintético no layout esperado (via Node) + import JSON
  como caminho alternativo sempre disponível. Validação final com PDF real fica para
  o responsável na primeira importação (a tela de preview/confirmação mitiga).
- **Empacotamento PyInstaller da v4**: adiado — não bloqueia o MVP; quando decidido,
  replicar o padrão do `build.yml` da v3 apontando para `inad-v4/run.py`.
- **Migração dos dados reais**: rodar `migrar_v3.py` na máquina do responsável
  (o banco real nunca passa por esta sessão).
- O merge commit `6cf9bf5` na base do branch é do próprio GitHub (web-flow) — não
  amendar; commits novos usam a identidade correta configurada no repositório local.
