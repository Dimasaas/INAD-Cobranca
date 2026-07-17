# 🏡 INAD — Painel de Cobrança

Esta é uma ferramenta interna desenvolvida para facilitar o contato e cobrança dos clientes inadimplentes. A ferramenta reúne os dados cadastrais dos clientes, permite a importação direta de relatórios em PDF, gera as mensagens de cobrança personalizadas para o WhatsApp e acompanha o status de envio de forma simples.

> [!NOTE]
> **Privacidade Garantida:** Este repositório não armazena nenhuma informação confidencial ou dados de clientes no histórico do Git. Todos os dados são processados localmente no seu computador.

---

## 💻 Como Baixar e Executar (Tutorial para Colaboradores)

### Passo 1: Baixar a Ferramenta
1. No topo desta página do GitHub, clique na aba **Releases** (lado direito) ou em [Releases](../../releases).
2. Baixe a versão mais recente correspondente ao seu sistema:
   *   **Para Windows:** Baixe o arquivo `INAD_Cobranca-Windows.zip`.
   *   **Para macOS:** Baixe o arquivo `INAD_Cobranca-macOS.zip`.
3. Extraia o arquivo `.zip` baixado em uma pasta de sua preferência.

---

### Passo 2: Executar no Windows 🪟
1. Abra a pasta onde você extraiu os arquivos.
2. Dê dois cliques no arquivo **`INAD_Cobranca.exe`** (ele possui um ícone de console).
3. Uma janela preta do console abrirá e, em seguida, seu navegador de internet abrirá automaticamente na ferramenta.
4. **Pronto!** Você já pode usar a ferramenta. 
   *   *Nota:* Mantenha a janela preta do console aberta enquanto estiver usando o painel. Para encerrar, basta fechar a janela preta.

---

### Passo 3: Executar no macOS (Mac) 🍏
Devido aos sistemas de segurança do macOS (Gatekeeper), aplicativos baixados da internet que não são assinados digitalmente exigem uma permissão simples na primeira execução:
1. Abra a pasta onde extraiu os arquivos.
2. **Não dê dois cliques diretamente.** Em vez disso, **clique com o botão direito** (ou segure a tecla `Control` e clique) no executável **`INAD_Cobranca`** e selecione **Abrir (Open)**.
3. O macOS exibirá um aviso dizendo que o "desenvolvedor não pode ser verificado". Clique no botão **Abrir (Open)** na caixa de diálogo para confirmar.
4. Uma janela do Terminal abrirá e a ferramenta será carregada automaticamente no seu navegador.
5. *Dica:* Esse procedimento do botão direito é necessário **apenas na primeira vez**. Nas próximas vezes, você poderá abrir o arquivo normalmente com dois cliques.
6. Mantenha a janela do Terminal aberta durante o uso. Feche-a para encerrar o servidor.

---

## 🛠️ Como Usar a Ferramenta

1. **Importar o Relatório PDF:** Clique no botão **📂 Importar PDF** no topo do painel e selecione o arquivo PDF do relatório de inadimplência (ou arraste e solte o PDF na tela).
2. **Visualizar a Barra de Carregamento:** Uma barra de progresso mostrará o status da leitura do PDF. Quando concluir, todos os clientes serão exibidos na tela organizados em cards.
3. **Enviar Mensagens:** Cada cliente terá o seu card contendo a mensagem pré-formatada. Clique em **Abrir WhatsApp** para abrir a conversa com a mensagem já digitada no celular ou WhatsApp Web.
4. **Marcar como Enviado:** O sistema marca o cliente como enviado automaticamente ao abrir o link do WhatsApp, mas você também pode marcar/desmarcar clicando no botão de check (✓).

---

## ⚙️ Para Administradores (Rodando via Código)

Se você deseja rodar ou atualizar a ferramenta a partir do código-fonte:

### Requisitos
- Python 3.x instalado.

### Executar o Servidor de Desenvolvimento
1. Abra o terminal na pasta do projeto.
2. Execute o comando:
   ```bash
   python3 run.py
   ```
3. O servidor local será iniciado e abrirá a ferramenta em `http://localhost:8000/inad_whatsapp.html`.

### Atualizar a Base de Clientes Iniciais Padrão
Se desejar embutir novos dados iniciais na página principal:
1. Substitua o conteúdo do arquivo `clients_data.json` na raiz da pasta.
2. Execute o script de importação:
   ```bash
   python3 add_pdf_importer.py
   ```
3. O arquivo `inad_whatsapp.html` será regenerado com os novos dados padrão embutidos.
