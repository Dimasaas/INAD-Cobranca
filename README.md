# 🏡 INAD — Painel de Gestão e Cobrança de Inadimplência

[![Python Version](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite Database](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Frontend UI](https://img.shields.io/badge/UI-Glassmorphism_Premium-purple)](https://developer.mozilla.org/en-US/docs/Web/CSS)
[![Build Tool](https://img.shields.io/badge/Bundler-PyInstaller-yellow)](https://pyinstaller.org/)
[![Platform Supported](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-brightgreen)](#)

Esta é uma ferramenta profissional de CRM e Gestão de Cobrança desenvolvida para automatizar e otimizar o fluxo de recuperação de inadimplência da construtora. O sistema sincroniza relatórios nativamente via **API da SeniorCloud (ProUAU)**, calcula scores de risco inteligentes, classifica devedores em réguas de cobrança operacionais e facilita contatos dinâmicos via WhatsApp Web.

> [!IMPORTANT]
> **Privacidade & Segurança:** Toda a manipulação de dados é realizada **localmente no seu computador** ou na **Intranet da sua empresa** — nunca na nuvem. O banco de dados SQLite (`inad_database.db`) fica no próprio servidor local. Acesso pela rede exige cadastro individual por operador (sem usuário/senha compartilhado) e fica registrado numa trilha de auditoria interna. Veja [🔒 Segurança e Privacidade](#-segurança-e-privacidade) abaixo.

---

## 📐 Arquitetura do Sistema

O INAD possui uma arquitetura híbrida ultra-leve que permite rodar tanto de forma 100% offline (local no navegador) quanto integrada com um servidor centralizado:

```mermaid
graph TD
    subgraph Frontend ["Frontend (HTML5 / Glassmorphism UI)"]
        UI["Painel de Cobrança / index.html"]
        ANA["Painel Científico / inad_analytics.html"]
        JS["Motor JS Local / localStorage"]
    end

    subgraph Backend ["Backend (Servidor run.py)"]
        API["Servidor HTTP Integrado"]
        SYNC["Integração UAU (SeniorCloud)"]
        DB[("Banco SQLite / WAL Mode")]
    end

    API_UAU["API ProUAU"] -->|GET (Sincronização)| SYNC
    SYNC -->|Mapeamento| DB
    UI -->|Registrar Log /api/actions/sent| API
    UI -->|Salvar Desfecho /api/outcomes| API
    API -->|Persistir dados| DB
    DB -->|Fila de Risco /api/queue| UI
    DB -->|Evolução de KPIs /api/kpis| ANA
    JS -.->|Fallback Offline| UI
```

---

## ✨ Funcionalidades Principais

- 📊 **Fila de Prioridades Inteligente (`/api/queue`):** Ordenação dinâmica de clientes calculada a partir do cruzamento de valor devedor (P90), dias de atraso (aging) e taxa de reincidência de relatórios.
- 📋 **Worklist Operacional:** Categorização imediata de clientes que precisam de ação urgente:
  - *Promessas Vencidas:* Clientes que prometeram pagar mas não quitaram no prazo da promessa.
  - *Recontato Agendado:* Agendas de ligação e follow-up automáticos para o dia corrente.
  - *Sem Resposta:* Clientes contatados e sem respostas registradas (agrupados automaticamente após envio de WhatsApp).
  - *Novos no Pré-Jurídico:* Devedores que acabam de ultrapassar a barreira crítica dos 120 dias.
- 💬 **WhatsApp Dinâmico Integrado:** Mensagens customizadas geradas automaticamente, incluindo variáveis de saudação baseadas em gênero, identificação do lote/quadra e saldo devedor atualizado, com link direto de disparo.
- 📝 **Registro de Desfechos (Outcomes):** Painel interno em cada card para cadastrar retornos das conversas (*Prometeu Pagar*, *Negociação*, *Recusou*, *Sem resposta*) no formato brasileiro `DD/MM/AAAA`.
- 📈 **KPIs com precisão exata:** identidade de cliente resiliente a variação de acento/caixa/espaço, somas monetárias em centavos inteiros (sem drift de ponto flutuante), e taxa de recuperação reportada em duas leituras lado a lado — quantos clientes "saíram da lista" vs. quantos têm pagamento efetivamente confirmado.
- 📁 **Auditoria e Logs de Erro:** Toda consulta ao perfil individual de um cliente (que expõe CPF/telefone) fica registrada — quem acessou, qual cliente, quando — consultável via API. Exceções de sistema ficam em `inad_errors.log` (multiplataforma).

---

## 💻 Como Baixar e Executar (Tutorial para Colaboradores)

### Passo 1: Obter a Aplicação
1. Vá até a aba de **Releases** do repositório no GitHub.
2. Baixe `INAD_Cobranca-Windows.zip` (build oficial distribuído — Windows 10/11).
3. Extraia o conteúdo do zip em uma pasta permanente (ex.: `C:\INAD\`).

> Quer rodar em macOS/Linux, ou embutir dados de exemplo no `.exe`? Veja
> [⚙️ Para Desenvolvedores](#️-para-desenvolvedores-rodando-via-código) —
> não há build oficial pra essas plataformas, mas compilar você mesmo é
> simples.

---

### Passo 2: Executar no Windows 🪟 (uso individual, só neste computador)
1. Abra a pasta extraída.
2. Execute o arquivo **`INAD_Cobranca.exe`** (clicando duas vezes).
3. Uma tela preta de terminal se abrirá no background, e o seu navegador de internet padrão abrirá automaticamente o Painel de Cobrança.
4. *Importante:* Mantenha o terminal aberto enquanto estiver trabalhando. Ao finalizar, basta fechar a janela preta para desligar o sistema.

Por padrão o servidor só aceita conexões deste mesmo computador — ninguém
mais na rede consegue acessar. Isso é proposital (ver seção de segurança
abaixo).

---

### Passo 3: Rodando como servidor de Intranet 🌐 (compartilhar com a equipe)
Pra deixar o painel acessível a partir de outros computadores da mesma rede
(escritório), é preciso: (a) ligar o servidor num modo que aceite conexões
de rede (`INAD_HOST=0.0.0.0` em vez do padrão localhost-apenas), e (b)
cadastrar um operador — com um token individual, não usuário/senha — para
cada pessoa que vai acessar. O passo a passo completo, com todos os
comandos, está em **[`TUTORIAL_INTRANET_WINDOWS.md`](./TUTORIAL_INTRANET_WINDOWS.md)**
(incluído dentro do zip da release) — escrito como um procedimento
executável tanto por uma pessoa quanto por um agente de automação/IA.

---

## 🔒 Segurança e Privacidade

- **Bind local por padrão:** o servidor só aceita conexões de `127.0.0.1`
  (o próprio computador) a menos que seja explicitamente exposto na rede
  (`INAD_HOST=0.0.0.0` ou `--host`).
- **Autenticação por operador:** expor na rede exige cadastrar pelo menos
  um operador (`INAD_Cobranca.exe --add-operator "Nome"`) — cada pessoa
  recebe um token individual (não há usuário/senha compartilhado). O
  servidor recusa subir exposto sem isso.
- **Trilha de auditoria:** toda consulta ao perfil individual de um
  cliente (que expõe CPF/telefone/endereço) fica registrada — quem, qual
  cliente, quando — consultável via `GET /api/audit`.
- **Identidade de cliente resiliente:** variação de acento/caixa/espaço no
  nome não cria um cliente "fantasma" nem distorce taxas de recuperação.
- **Sem criptografia própria do banco:** o `inad_database.db` não é
  criptografado pela aplicação (decisão deliberada — ver `AI_CONTEXT.md`);
  a proteção do disco (BitLocker/FileVault) fica a cargo do sistema
  operacional. Não recomendado expor esta máquina fora da rede local da
  empresa (sem VPN pública, sem port forwarding).
- **Nunca vai para a nuvem:** todos os dados (relatórios, banco, backups)
  ficam só no computador onde o servidor roda.

Para o passo a passo completo de configurar acesso pela rede com múltiplos
operadores, veja [`TUTORIAL_INTRANET_WINDOWS.md`](./TUTORIAL_INTRANET_WINDOWS.md).

---

## ⚙️ Para Desenvolvedores (Rodando via Código)

### Instalação de Requisitos e Configuração (API UAU)
O sistema puxa relatórios nativamente da API ProUAU. Crie um arquivo `.env` na raiz do projeto com as chaves:

```env
UAU_BASE_URL=https://gamma-api.seniorcloud.com.br:51910/uauAPI
UAU_USUARIO=seu_usuario
UAU_SENHA=sua_senha
UAU_X_INTEGRATION=seu_token_jwt
```

Instale o Python 3.8+ em sua máquina e garanta o driver SQLite padrão ativo. Para iniciar o servidor de desenvolvimento local:

```bash
# Iniciar o servidor com a base de dados real
python run.py

# Iniciar o servidor escutando em uma porta específica
python run.py --port 9090
```
O painel abrirá automaticamente no endereço correspondente.

### Estrutura dos Arquivos Principais
- `run.py`: Servidor HTTP/API REST nativo em Python com SQLite em modo WAL e gerenciamento de erros estruturado.
- `index.html`: Interface do Painel de Cobrança contendo o Design System (CSS), cards ricos e lógica JS da aplicação.
- `inad_analytics.html` / `analytics.js`: Dashboard de inteligência estatística para análise de recuperação.
- `inad_errors.log`: Arquivo gerado automaticamente em caso de exceções não tratadas no servidor para fins de suporte técnico.

### Compilando Binários
O build oficial (Windows) é gerado automaticamente pelo GitHub Actions a
cada tag `v*` (veja `.github/workflows/build.yml`). Para compilar
manualmente (Windows, macOS ou Linux):
```bash
pip install pyinstaller
python add_pdf_importer.py   # gera inad_whatsapp.html
pyinstaller --onefile --add-data "inad_whatsapp.html;." --add-data "libs;libs" --name INAD_Cobranca run.py
```
*(No macOS/Linux, troque o `;` do argumento `--add-data` por `:`).*

O executável gerado (`dist/INAD_Cobranca`) deve ficar na mesma pasta que
`inad_whatsapp.html`, `inad_analytics.html`, `analytics.js`, `analytics.css`
e `libs/` — esses arquivos não ficam embutidos no `.exe`, são lidos do
disco ao lado dele (é assim que o banco de dados e os logs também
persistem entre execuções, mesmo empacotado com `--onefile`).
