# INSTRUÇÕES DE REFORMA — KPIs, Completude de Relatório e Legendas

> **Para quem vai implementar (agentes/devs).** Este documento é a especificação
> da reforma pedida pelo responsável. Aplica-se ao código **v3 na raiz** do
> repositório (`run.py`, `inad_template.html`, `inad_analytics.html`,
> `analytics.js`). É permitido reestruturar do zero, desde que **duas restrições**
> sejam mantidas (ver §8). O propósito da ferramenta **não muda**: importar
> relatório(s) de inadimplência do **ProUAU** (PDF) e gerar CRM de cobrança
> (WhatsApp) + KPIs de recuperação.
>
> Contexto técnico do que já existe: `AI_CONTEXT.md` e `HANDOFF.md`.
> A pasta `inad-v4/` (reescrita) foi **abandonada** pelo responsável — estas
> instruções a substituem. Pode ignorar/remover `inad-v4/`.

---

## 0. O problema em uma frase

O sistema hoje **(a) mistura, num mesmo número de KPI, dados factuais do
relatório com dados que a equipe digitou** (contatos, desfechos), e
**(b) trata todo relatório como se fosse uma foto completa da inadimplência** —
quando na prática alguns relatórios do ProUAU são parciais (filtrados por
intervalo de parcelas, por data de corte, etc.), o que **distorce silenciosamente**
toda comparação entre relatórios (recuperação, evolução, segmentação).

A reforma tem **3 frentes**, nesta ordem de prioridade:

1. **Completude de relatório** (§2) — a mais crítica. Sem isso, os KPIs de
   recuperação mentem.
2. **Separação factual × operacional** (§3) — nunca mais um KPI que some as duas
   naturezas de dado.
3. **Dicionário/legenda de KPIs** (§4) — todo indicador explicado ao usuário.

---

## 1. Os dois universos de dados (fundamento de tudo)

Todo dado do sistema pertence a **exatamente um** destes universos. Nenhum KPI
pode fundir os dois num único número.

### Universo A — FACTUAL (vem do PDF do ProUAU)
Sai direto do relatório importado. Não depende de nenhuma ação humana.
- Tabelas atuais: `reports`, `clients`, `properties`, `parcels`.
- Campos: nome, CPF/CNPJ, telefone, e-mail, venda_id, identificador, parcela,
  vencimento, valor.
- **Afetado apenas por: completude do relatório** (§2).

### Universo B — OPERACIONAL (a equipe digitou/registrou)
Reflete o esforço de cobrança da equipe, não o relatório.
- Tabelas atuais: `action_logs` (disparos de WhatsApp), `contact_outcomes`
  (desfechos: prometeu_pagar, pagou, negociação, recusou, sem_resposta,
  numero_invalido, outro), `kpi_exclusions` (ajuste manual).
- **Afetado por: quão diligente a equipe foi ao registrar.** Pode estar
  incompleto por falha de registro, não por falha do relatório.

### Regra de ouro
> Um KPI factual **nunca** pode ser alterado por dado do Universo B, e vice-versa.
> Onde hoje eles se misturam, **separar** (ver §3 para a lista exata dos pontos).

---

## 2. FRENTE 1 — Completude / escopo de relatório (CRÍTICO)

### 2.1 O que muda no modelo de dados
Adicionar à tabela `reports` (migração idempotente no padrão `PRAGMA table_info`
+ `ALTER TABLE` já usado em `init_db()`):

```sql
ALTER TABLE reports ADD COLUMN escopo TEXT DEFAULT 'nao_confirmado';
  -- valores: 'completo' | 'parcial' | 'nao_confirmado'
ALTER TABLE reports ADD COLUMN escopo_motivo TEXT DEFAULT '';
  -- ex.: 'filtrado 1-3 parcelas', 'corte por data 2026-03-01', 'segmento X'
ALTER TABLE reports ADD COLUMN escopo_origem TEXT DEFAULT '';
  -- 'declarado_usuario' | 'detectado_pdf' | 'heuristica' | ''
```

### 2.2 Como determinar o escopo (3 camadas, em ordem de confiança)

**(a) Declaração do usuário no import — FONTE DA VERDADE.**
Como o usuário importa **vários relatórios de uma vez** (recurso que DEVE ser
mantido — §8), a tela de import precisa, **para cada arquivo**, deixar marcar:
- ⚪ Relatório **completo** (toda a inadimplência naquela data), ou
- ⚪ Relatório **parcial** + campo de motivo (ex.: "só parcelas 1 a 3").

A heurística (c) **pré-seleciona** a opção, mas o usuário confirma. O usuário
SABE o escopo na hora de emitir no ProUAU — essa é a informação mais confiável.

**(b) Detecção automática pelo PDF — SE o ProUAU estampar o filtro.**
O parser atual (`parseLayoutText`/`extractReportDate` em `inad_template.html`)
**só captura a data** e descarta o resto do cabeçalho. Instrução: **capturar
qualquer linha de cabeçalho que descreva o critério de emissão** (intervalo de
parcelas, data de corte, situação, filtro de segmento) e gravar em
`escopo_motivo` com `escopo_origem='detectado_pdf'`.
> ⚠️ **AÇÃO NECESSÁRIA DO RESPONSÁVEL:** fornecer 1 PDF **completo** e 1 PDF
> **parcial (ex.: 1–3 parcelas)** do ProUAU para os agentes descobrirem
> **exatamente** que texto o ProUAU imprime no cabeçalho. Sem isso, a camada (b)
> não pode ser calibrada e o sistema depende de (a) e (c). NÃO inventar o formato.

**(c) Heurísticas de alerta — REDE DE SEGURANÇA (sempre roda).**
Mesmo sem declaração, o sistema deve **desconfiar e avisar**. Sinais de possível
incompletude (calibrar limiares com dados reais — ver ação acima):
- **Todo cliente tem ≤3 parcelas** → quase certamente relatório filtrado por
  intervalo de parcelas. (Sinal mais forte e direto para o caso citado.)
- **Queda brusca de nº de clientes** vs. a mediana dos últimos N relatórios
  `completo` (ex.: −40%).
- **Máximo de dias de atraso muito menor** que o histórico → provável corte por
  data recente.
- **Total devido muito abaixo** da tendência dos relatórios completos.
Cada sinal disparado: marca `escopo='nao_confirmado'` + `escopo_motivo` descritivo
+ `escopo_origem='heuristica'`, e **força um aviso visível** (§2.4) pedindo
confirmação do usuário.

### 2.3 Regra de cálculo (o coração da correção)

Classificar **todo KPI** em duas categorias e aplicar a regra:

**KPIs INTRA-relatório** (descrevem UM único relatório):
total devido, nº clientes, nº parcelas, aging/buckets, dias médios de atraso,
top devedores, fila de risco, score, distribuição por estágio.
→ **Podem rodar em qualquer relatório, inclusive parcial.** Mas a UI **deve
rotular** de qual relatório vieram e o escopo dele ("referente ao Relatório de
15/03 — ⚠ parcial: só 1–3 parcelas").

**KPIs ENTRE-relatórios** (comparam dois ou mais relatórios):
recuperação (`recovery_rate` bruta), migração de aging, evolução temporal,
segmentação novo/antigo, reincidência/reentradas.
→ **Só podem usar relatórios `escopo='completo'`.** Regra simples e segura:
- Montar a série temporal **apenas** com relatórios `completo`.
- **Nunca** comparar `completo` × `parcial` (é isso que hoje infla a recuperação:
  clientes "somem" porque o relatório seguinte foi filtrado, não porque pagaram).
- Relatórios `parcial` e `nao_confirmado` são **pulados** na série, e o sistema
  **lista quais foram pulados e por quê** (§2.4).
- Se houver **< 2 relatórios completos**, KPIs temporais retornam
  `{"status": "dados_insuficientes", "motivo": "..."}` — **nunca** `0`, `NaN`,
  divisão por zero, ou um número enganoso.

### 2.4 Alertas na UI (pedido explícito do responsável)

- **Banner no topo da aba KPIs/Analytics:**
  *"Cálculos de recuperação usam N de M relatórios. Excluídos: [15/03 — parcial:
  só 1–3 parcelas], [02/04 — escopo não confirmado]."* — clicável, abre detalhes.
- **Na tabela de relatórios:** badge por linha — `Completo ✓` (verde) /
  `Parcial ⚠` (âmbar, com motivo no hover) / `Escopo não confirmado ⚠` (cinza,
  com botão "Confirmar escopo").
- **No gráfico de evolução:** relatórios não-completos aparecem com marcador
  distinto (ponto oco/tracejado) ou omitidos, **sempre com nota** — nunca
  desenhados como se fossem parte da série válida.
- **Ação de correção sempre disponível:** o usuário pode reclassificar o escopo
  de um relatório a qualquer momento (endpoint `POST /api/reports/<id>/escopo`),
  e os KPIs recalculam.

### 2.5 À prova de quebra
Nenhum caminho pode: dividir por zero, assumir escopo, incluir parcial em cálculo
temporal, ou falhar em silêncio. Todo relatório fora do cálculo temporal **tem
que aparecer** na lista de excluídos com motivo.

---

## 3. FRENTE 2 — Separar factual × operacional nos KPIs

### 3.1 Onde eles se misturam HOJE (mapa exato para corrigir)
| Local no código | Mistura | Ação |
|---|---|---|
| `/api/summary` (objeto único) | `current/trend/aging/pre_juridico/top_debtors` (A) juntos com `effectiveness` + `worklist_counts` (B) | Reestruturar resposta em `factual: {...}` e `operacional: {...}` |
| `get_kpis_data()` / `get_analytics_data()` → `recovery_rate_confirmed` | "saiu" (A) + desfecho `pagou` (B) | Mover `recovery_rate_confirmed` para o bloco operacional; deixar `recovery_rate` bruta no factual |
| `_contact_effectiveness()` | `action_logs`+`contact_outcomes` (B) cruzados com presença no relatório (A) | É KPI **operacional** — só na seção operacional, rotulado como "depende de registro da equipe" |
| Filtro `NOT IN kpi_exclusions` em todo cálculo factual | Ajuste humano (B) invisível dentro de números factuais | Manter a exclusão, mas torná-la **visível**: "N clientes excluídos manualmente [lista]" ao lado do KPI factual |

### 3.2 A UI passa a ter DUAS seções claramente rotuladas
1. **"Indicadores do Relatório (factuais)"** — só Universo A.
   Legenda fixa: *"Calculados diretamente do PDF do ProUAU. Não dependem de
   nenhuma ação da equipe."*
2. **"Indicadores Operacionais (equipe)"** — só Universo B.
   Legenda fixa: *"Dependem do que a equipe registrou (contatos, desfechos).
   Refletem o esforço de cobrança, não apenas o relatório — podem estar
   subestimados se algum registro deixou de ser feito."*

A `worklist`/fila continua sendo ferramenta operacional do dia a dia (não é KPI
histórico) — pode ficar na sua própria aba, sem contaminar os indicadores factuais.

---

## 4. FRENTE 3 — Dicionário / legenda de todos os KPIs

### 4.1 Entrega
- Backend: uma constante `KPI_DICIONARIO` servida em `GET /api/kpis/dicionario`,
  cada item `{id, nome, definicao, formula, universo, observacoes}`.
- UI: um ícone `?` ao lado de **cada** KPI, que abre a definição vinda do
  dicionário (tooltip/popover). Nada de número sem explicação a um clique.

### 4.2 Textos das legendas (usar como estão; ajustar se a fórmula mudar)

**FACTUAIS (Universo A):**
- **Total inadimplente** — Soma de todas as parcelas vencidas no relatório, em
  reais. *Factual.* Só do relatório exibido.
- **Clientes em atraso** — Nº de clientes distintos com ao menos uma parcela
  vencida no relatório. *Factual.* Identidade por CPF/nome normalizado.
- **Parcelas em atraso** — Contagem de parcelas vencidas no relatório. *Factual.*
- **Dias médios de atraso** — Média, entre os clientes, do maior atraso de cada
  um (dias desde a parcela vencida mais antiga). *Factual.*
- **Distribuição por aging** — Nº de clientes e valor em cada faixa de atraso
  (0–30, 31–60, 61–90, 91–120, 121+ dias). *Factual.*
- **Top devedores** — Clientes ordenados pelo total devido. *Factual.*
- **Evolução** — Total devido / nº de clientes ao longo dos relatórios
  **completos**. *Factual.* ⚠ Só relatórios completos entram (§2.3).
- **Taxa de recuperação (saiu da lista)** — % de clientes de um relatório
  completo que **não aparecem** no relatório completo seguinte.
  Fórmula: `(clientes em Rₙ ∉ Rₙ₊₁) / total em Rₙ × 100`.
  *Factual, porém interpretar com cuidado:* "sumiu" **não** é sinônimo de "pagou"
  — pode ser renegociação fora do sistema, encaminhamento ao jurídico, ou dado
  ausente. Para pagamento confirmado, ver o indicador operacional abaixo.
- **Reincidência** — Nº de vezes que o cliente saiu e voltou à lista entre
  relatórios completos. *Factual.*
- **Segmentação novo/antigo** — "Novo" = estreou na lista na data de corte ou
  depois; "antigo" = já aparecia antes. *Factual.*

**OPERACIONAIS (Universo B):**
- **Recuperação confirmada** — % de clientes que saíram da lista **e** têm um
  desfecho `pagou` registrado pela equipe. *Operacional.* Subestima se a equipe
  não registrou todos os pagamentos.
- **Eficácia de contato** — % de clientes que regularizaram (sumiram do relatório
  completo seguinte) **após** um disparo de WhatsApp registrado. *Operacional.*
- **Promessas feitas / cumpridas** — Nº de desfechos `prometeu_pagar` e, destes,
  quantos o cliente saiu da lista no relatório completo seguinte. *Operacional.*
- **Clientes excluídos dos KPIs** — Nº de clientes que a equipe marcou para
  ignorar manualmente. *Ajuste operacional* — mostrado ao lado dos factuais para
  transparência.

**AJUSTE/RISCO (derivado, expor a fórmula):**
- **Score de risco (0–100)** — Ordenador da fila de cobrança.
  `45%·(valor/P90) + 35%·(atraso/180d) + 20%·(reincidência/3)`, cada componente
  limitado a 1. P90 = valor devido do percentil 90 da carteira. *Heurístico de
  priorização, não probabilidade de pagamento.* Pesos configuráveis. Exibir os
  três componentes junto do número, para ser explicável.

---

## 5. Migração dos dados v3 existentes
- Relatórios já importados não têm escopo. Na migração: marcar todos como
  `nao_confirmado`, rodar as heurísticas (§2.2c) para sinalizar suspeitos, e
  apresentar ao usuário uma tela de **confirmação em lote** ("revise o escopo dos
  seus N relatórios existentes"). **Não** assumir `completo` automaticamente.
- Nenhum dado factual ou operacional é apagado — só ganha a classificação de escopo.

---

## 6. Testes obrigatórios (golden — provam cada regra)
Estender `tests/test_golden_kpis.py`:
- **Completude:** relatório completo → parcial (só 1–3 parcelas) → a recuperação
  **não** conta os clientes ausentes do parcial; o parcial aparece na lista de
  excluídos com motivo; KPIs intra-relatório do parcial ainda funcionam.
- **< 2 completos** → KPIs temporais retornam `dados_insuficientes`, nunca 0/erro.
- **Heurística "≤3 parcelas"** dispara aviso de possível parcial.
- **Separação:** um desfecho `pagou`/contato registrado **não** altera nenhum KPI
  factual (total, aging, nº clientes, recuperação bruta); altera só os operacionais.
- **Reclassificar escopo** recalcula os KPIs temporais corretamente.
- **Dicionário:** todo KPI exposto na UI tem entrada correspondente em
  `/api/kpis/dicionario` (teste de cobertura — nenhum KPI órfão).
Rodar a suíte inteira ANTES e DEPOIS (padrão já estabelecido no projeto).

---

## 7. Roteiro de execução sugerido (para os agentes)
1. Migração de schema (`escopo`/`escopo_motivo`/`escopo_origem`) + backfill
   `nao_confirmado` + heurísticas.
2. Camada de cálculo: função única `serie_relatorios_completos()` usada por TODOS
   os KPIs temporais (elimina a lógica de dedup duplicada em ~5 lugares — foi
   causa de bug no passado, ver K4 no HANDOFF).
3. Reestruturar `/api/summary`, `/api/kpis`, `/api/kpis/analytics` em blocos
   `factual` / `operacional`, com metadados de relatórios usados/excluídos.
4. `GET /api/kpis/dicionario` + `POST /api/reports/<id>/escopo`.
5. Import múltiplo: adicionar etapa de escopo por arquivo (mantendo o fluxo atual).
6. Frontend: duas seções rotuladas, banners de exclusão, badges de escopo,
   tooltips `?` do dicionário, marcadores no gráfico de evolução.
7. Testes golden (§6) + smoke E2E via HTTP + conferência visual.

---

## 8. Restrições invioláveis
1. **Manter o import de múltiplos relatórios de uma vez** — a reforma **acrescenta**
   a etapa de escopo por arquivo, mas o fluxo de selecionar/arrastar vários PDFs
   de uma vez **permanece**.
2. **Propósito inalterado** — importar relatório(s) do ProUAU → CRM de cobrança
   (WhatsApp) + KPIs de recuperação.
3. **Sem PII no código-fonte** — não reintroduzir nomes/telefones reais (o
   histórico do git foi limpo disso; ver S9 no HANDOFF). Parser deve ser genérico.
4. **Mensagens de cobrança conforme CDC art. 42** — factuais e respeitosas;
   pré-jurídico "poderá ser encaminhado ao setor jurídico", nunca ameaça de
   processo/negativação/perda do imóvel.
5. **Rodar a suíte de testes antes e depois** de qualquer mudança em KPI.

---

## 9. Pendências que dependem do responsável (não bloqueiam o resto)
- **Fornecer PDFs de exemplo** (1 completo + 1 parcial de 1–3 parcelas) para
  calibrar a detecção automática de escopo e os limiares das heurísticas (§2.2).
- **Confirmar os limiares** das heurísticas depois de ver dados reais (ex.: qual
  % de queda de clientes conta como "suspeito").
- Decidir se relatórios `parcial` devem aparecer na **fila operacional** do dia
  (recomendado: sim — servem para trabalhar aquele recorte) ou não.
