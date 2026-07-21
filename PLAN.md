# PLAN — Plano de produto, KPI e execução (INAD)

> **O que é este arquivo:** o plano estratégico do INAD — do design ao KPI — e o
> método de execução com IA. Fica **acima** do `HANDOFF.md` (que rastreia o
> backlog em andamento com arquivo/linha exatos). Ordem de leitura para uma
> sessão nova (Sonnet ou humano):
> `CLAUDE.md` → `AI_CONTEXT.md` (arquitetura, schema, API, fórmulas) →
> `HANDOFF.md` (estado da revisão) → **este arquivo** (para onde vamos e por quê).

---

## 0. Norte do produto (critério único de sucesso)

**A ferramenta existe para REDUZIR a inadimplência — não para medi-la.**
Toda decisão de design, KPI e priorização é avaliada por: mais recuperação
(`pagou`), resolução mais rápida, e queda do saldo em atraso (R$) ao longo do
tempo. Métrica que não leva a uma ação concreta dos sócios é peso morto e não
entra na tela principal.

Contexto de uso: **intranet, 3 sócios**, rodando num notebook Windows 10 em
`127.0.0.1`. Simplicidade e robustez local valem mais que escala.

---

## 1. Princípios de design (onde está a alavanca)

A arquitetura atual (servidor Python puro + SQLite + frontend compilado) é
sólida e adequada ao cenário. A alavanca de redução **não** é mais feature — é
**menos fricção na decisão do operador**:

1. **Fila única e ranqueada** — "quem cobrar hoje e por quê" — em vez de o sócio
   escolher no olho. Já existe: `/api/queue` (por `risk_score`) e
   `/api/worklist` (alertas: `promessas_vencidas`, `recontato_agendado`,
   `sem_resposta`, `novos_pre_juridico`). O design deve **empurrar** para ela.
2. **Registrar desfecho em 1 clique.** Os KPIs de recuperação só valem se
   `contact_outcomes` for alimentado. Fricção no log = KPI cego = loop de
   redução quebrado. **É o maior retorno de UX do projeto.**
3. **Fechar o loop rápido.** Promessas vencidas e "sem resposta" são o motor:
   perseguir antes do caso envelhecer para `pre_juridico`.
4. **Transparência de KPI** (ver §3) — operador que entende a métrica age
   melhor; num time de 3 sócios nem todos são técnicos.

---

## 2. Hierarquia de KPI (orientada a reduzir, não a vaidade)

| Camada | KPI | Origem (já existe) | Move a inadimplência? |
|---|---|---|---|
| **North star (lagging)** | Saldo em atraso (R$) e nº de inadimplentes; tendência vs. relatório anterior | `/api/summary` → `current`, `trend` | Resultado final |
| **Resultado (médio)** | `recovery_rate`; valor recuperado (R$); % que regulariza após contato | `/api/kpis`; `/api/summary` → `effectiveness` | Sim |
| **Leading (controlável na semana)** | % dos top-risco contatados em ≤ N dias; `promises_kept_rate`; tempo até 1º contato; nº de novos casos entrando em `pre_juridico` (**quer ↓**) | `/api/queue`, `/api/worklist`, `effectiveness` | O que os sócios movem no dia a dia |
| **Guardrail (não violar)** | Compliance CDC art. 42 (só horário comercial, template factual, sem ameaça) | já enforced em `run.py`/templates | Protege a operação |

**Melhoria de KPI recomendada (opcional, item 4 do roadmap):** somar ao
`risk_score` (exposição) um sinal de **recuperabilidade** — cliente que
historicamente regulariza após contato é alto ROI mesmo com dívida menor.
Ranquear por "onde o contato mais muda o resultado", não só pela maior dívida.
Base já existe: `response_behavior` em `/api/clients/profile`.

---

## 3. Feature: KPIs legendados + página de documentação (pedido do responsável)

**Objetivo:** todo KPI/rótulo na tela deve ser autoexplicativo, e deve haver uma
página única que descreva **o que cada métrica é, a fórmula e como interpretar**
(o que é bom/ruim e para que serve na redução).

**Decisão de implementação (a mais simples e alinhada ao projeto):**
- **Página estática dedicada** `inad_kpis_docs.html` — no mesmo padrão de
  `inad_analytics.html` (**fora** do passo de compilação `add_pdf_importer.py`;
  servida direto pelo `run.py`, editável livremente). Sem framework, sem CDN
  obrigatório.
- **Botão 📖 "Entenda os KPIs"** no cabeçalho do painel principal
  (`inad_template.html` → recompilar) e da página de Analytics
  (`inad_analytics.html`), abrindo a página de docs em nova aba.
- **Rota amigável** no `run.py`: `/kpis-docs` / `/ajuda` → `inad_kpis_docs.html`
  (mesmo esquema de redirect 302 já usado para `/analytics`).
- **Legendas inline:** cada card de KPI ganha um tooltip curto (atributo
  `title=` ou ícone "ⓘ") com uma frase; o link "saiba mais" leva à seção
  correspondente na página de docs (âncora `#recovery-rate` etc.).

**Conteúdo da página (fórmulas — copiar de `AI_CONTEXT.md`, aqui já consolidado):**

- **Saldo em atraso / nº de inadimplentes** — soma dos valores em aberto e
  contagem de clientes no relatório de referência. Interpretação: é o
  north-star; cai = estamos ganhando. Origem: `/api/summary`.
- **Taxa de recuperação (`recovery_rate`)**
  `Taxa = (clientes em Rₙ que NÃO aparecem em Rₙ₊₁) / (total em Rₙ) × 100`.
  Interpretação: quanto maior, mais gente saiu da lista entre relatórios.
  Limitação conhecida (K6): "sumir do relatório" ≠ prova de pagamento — está
  documentado e é decisão de negócio futura.
- **Score de Risco (0–100)** `Score = 45·V + 35·A + 20·R`, onde
  `V = min(total_owed / P90_da_carteira, 1)` (exposição financeira),
  `A = min(max_days_overdue / 180, 1)` (idade do atraso mais antigo),
  `R = min(reentries / 3, 1)` (reincidência na lista).
  Interpretação: prioriza quem cobrar primeiro. É puramente matemático e
  explicável (mostrar os 3 componentes `V/A/R` na tela reforça a confiança).
- **Estágios de cobrança** (por `max_days_overdue`): `lembrete` (≤30),
  `firme` (31–90), `serio` (91–120), `pre_juridico` (>120, rótulo "121+").
  `pre_juridico` é **fila interna de triagem humana**, não automatiza nenhum
  ato jurídico (compliance).
- **Segmentação novo × antigo** — cliente é "novo" se sua **primeira aparição
  em todo o histórico** (`MIN(report_date)` por nome normalizado) foi na data
  de corte ou depois; senão "antigo". O corte é configurável (`cutoff` ou
  `cutoff_last_n`). Regra crítica: a 1ª aparição é sempre sobre o histórico
  inteiro, nunca restrita ao filtro de datas da tela.
- **Distribuição por aging (buckets)** — 0-30 / 31-60 / 61-90 / 91-120 / 121+
  dias, em nº de parcelas e R$. Interpretação: concentração em buckets altos =
  risco de perda.
- **Efetividade de contato** — `% regulariza após contato =
  regularized_after_contact / contacted`; `promises_kept_rate =
  promises_kept / promises_made` (com guarda contra divisão por zero, K9).
  Interpretação: mede se a cobrança está de fato convertendo.
- **Data de referência do atraso** — Operacional (fila/worklist/profile): atraso
  medido contra **hoje** (data local do servidor). Histórico (KPIs/Analytics):
  contra o **`report_date`** do relatório (reprodutível no tempo).

**Critério de aceitação:** todo card de KPI na tela tem legenda; a página de
docs cobre os itens acima; os botões/rotas funcionam; a página **não** quebra o
fallback offline do painel (ela depende do servidor, como a de Analytics — deixar
explícito). Nada de PII na página (é só metodologia).

---

## 4. Roadmap sequenciado (ligado ao objetivo de reduzir)

Ordem escolhida pelo impacto na *confiabilidade dos números de redução* primeiro.
Locais exatos (arquivo/linha) já estão no `HANDOFF.md` — **não duplicar aqui**.

1. **K2 — Normalização de nome** (aprovado, só esqueleto feito). Sem isso,
   "JOSÉ" ≠ "Jose" viram clientes distintos → `recovery_rate`, reincidência e
   risco ficam **errados**. Pré-requisito para confiar em qualquer número.
   *Aceitação:* teste golden com o mesmo cliente grafado diferente em 2
   relatórios contando como 1. **← primeiro.**
2. **K7 — Precisão monetária (centavos inteiros).** Faz o R$ bater exato (sem
   drift de arredondamento). Sem isso a métrica north-star mente na casa dos
   centavos. *Aceitação:* teste golden somando N parcelas conhecidas ao centavo.
3. **Feature §3 — KPIs legendados + página de docs.** Barata (página estática),
   melhora a ação dos sócios, sem risco de KPI. Pode ir em paralelo às demais.
4. **(Opcional) Next-best-action / recuperabilidade na fila** (§2).
5. **S6 — Auditoria de acesso / cripto at-rest.** Importante p/ LGPD, mas não
   move a inadimplência direto → **por último**; ainda depende de decisão do
   responsável (ver `HANDOFF.md`).

**K6 (`recovery_rate`) fica intocado** — redefinição é decisão de negócio,
documentada como limitação conhecida.

---

## 5. Método de execução com IA (Opus planeja, Sonnet executa)

- **Divisão de trabalho:** Opus (caro, raciocínio) planeja/arquiteta; **Sonnet
  (barato) implementa**. K2/K7 estão especificados no `HANDOFF.md` a ponto de o
  Sonnet executar com precisão — não gastar Opus para digitar código.
- **Um item por sessão Sonnet limpa.** Menor custo de contexto e menor risco.
  Ao fim de cada item: atualizar o `HANDOFF.md` (o que foi feito, o que falta).
- **Não usar workflow multi-agente ("ultracode no talo") para o backlog
  rotineiro** — queima o limite semanal (plano Pro/Max) para pouco ganho.
  Reservar para *uma* varredura ampla eventual (ex.: auditoria de segurança).
- **Continuidade quando o limite acabar:** não há "retomada" automática confiável
  após estourar o limite de 5h, e um agente agendado consome do **mesmo** limite.
  O mecanismo de handoff real é o `HANDOFF.md` + este `PLAN.md`: uma sessão nova
  lê os dois e continua de onde parou com custo mínimo de rampa. Quando o acesso
  voltar, é só abrir sessão Sonnet e apontar "continue pelo HANDOFF".

### Receita de arranque para uma sessão Sonnet nova (barata)
1. Ler `CLAUDE.md`, `HANDOFF.md`, este `PLAN.md` (não reler o `run.py` inteiro —
   ir por busca aos pontos citados no `HANDOFF.md`).
2. Rodar a suíte golden **antes** de mexer:
   `python -m unittest discover -s tests -v`.
3. Implementar **um** item (começar por K2). Escopo mínimo, sem refatorar o
   não relacionado.
4. Verificar: `python -c "import ast; ast.parse(open('run.py', encoding='utf-8').read())"`
   → suíte golden de novo → smoke test
   (`INAD_PORT=8999 INAD_HEADLESS=1 python run.py` + `curl /api/health`).
5. Atualizar `HANDOFF.md` e commitar com escopo focado.

---

## 6. Fora de escopo / não fazer

- **Reescrever o sistema do zero** — o código é maduro, testado e com
  segurança/compliance já feitos. Alterações são **focadas e incrementais**
  (regra do `AI_CONTEXT.md`).
- Mudar a forma da resposta de `/api/kpis` (a aba KPI depende dela) — features
  novas de análise vão em `/api/kpis/analytics`.
- Commitar `.db`/`-shm`/`-wal`, `.json` com dados reais ou PDFs (PII/LGPD).
- Automatizar qualquer ato jurídico (só triagem humana no `pre_juridico`).
