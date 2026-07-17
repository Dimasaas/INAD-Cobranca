# 🏡 INAD — Painel de Cobrança

Esta é uma ferramenta interna desenvolvida para facilitar o contato e cobrança dos clientes inadimplentes. A ferramenta reúne os dados cadastrais dos clientes, permite a importação direta de relatórios em PDF, gera as mensagens de cobrança personalizadas para o WhatsApp e acompanha o status de envio de forma simples.

---

## 🚀 Como Executar a Ferramenta

Existem três formas simples de rodar esta ferramenta no computador:

### Opção 1: Usando o Executável Compilado (Recomendado para Colaboradores)
Se o repositório estiver configurado no GitHub, você pode ir na aba **Releases** do repositório, baixar o executável para o seu sistema operacional (Windows/macOS) e abri-lo:
1. Baixe o arquivo `.zip` ou executável (`INAD_Cobranca.exe` ou `INAD_Cobranca`).
2. Execute o arquivo. Ele abrirá uma janela preta do console e abrirá automaticamente o painel de cobrança no seu navegador de internet padrão.
3. **Importante:** Mantenha a janela do console aberta enquanto estiver usando o painel. Para fechar, basta fechar o navegador e a janela do console.

### Opção 2: Rodando via Python (Para Desenvolvedores/Administradores)
Se você baixou a pasta completa do projeto e possui o Python instalado:
1. Abra a pasta do projeto no Terminal ou Prompt de Comando.
2. Execute o comando:
   ```bash
   python3 run.py
   ```
3. O painel abrirá automaticamente no navegador em `http://localhost:8000/inad_whatsapp.html`.

### Opção 3: Abrir diretamente no Navegador (Sem Instalação)
Você pode simplesmente dar dois cliques no arquivo `inad_whatsapp.html` para abri-lo diretamente:
- *Nota:* Devido a restrições de segurança dos navegadores, a importação de PDF sob o protocolo `file://` rodará na thread principal, o que pode causar um congelamento momentâneo de 1 a 2 segundos na tela durante a leitura de PDFs muito grandes. Para evitar isso, prefira usar a **Opção 1** ou **Opção 2**.

---

## 📂 Estrutura do Projeto

*   `inad_whatsapp.html`: O painel de cobrança completo. Contém os dados dos clientes e a lógica de importação do PDF.
*   `inad_template.html`: O template base da página web. **Não edite `inad_whatsapp.html` diretamente**; edite este template e depois regenere a ferramenta.
*   `add_pdf_importer.py`: Script Python que compila o `inad_template.html` junto com os dados atualizados de `clients_data.json` para gerar o `inad_whatsapp.html`.
*   `run.py`: Script que inicia o servidor local na porta 8000 para contornar restrições de segurança do navegador e habilitar processamento em background.
*   `libs/`: Pasta contendo a biblioteca PDF.js local para permitir a execução 100% offline.
*   `clients_data.json`: Arquivo contendo a base de dados padrão dos clientes inadimplentes.
*   `.github/workflows/build.yml`: Configuração do GitHub Actions que compila automaticamente o executável para Windows e macOS a cada nova versão criada no repositório Git.

---

## 🛠️ Como Atualizar a Base de Clientes

Sempre que a base de dados de clientes mudar ou se você quiser atualizar os dados iniciais padrão embutidos na ferramenta:
1. Substitua ou edite as informações no arquivo `clients_data.json`.
2. No terminal, execute o comando:
   ```bash
   python3 add_pdf_importer.py
   ```
3. O arquivo `inad_whatsapp.html` será regenerado com os novos dados embutidos.

---

## ⚙️ Como Gerar o Executável Manualmente (Compilação Local)

Se você precisar compilar o executável manualmente no seu computador sem usar o GitHub Actions:
1. Instale o PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Compile o executável incluindo a pasta `libs/` e os arquivos HTML:
   *   **No Windows:**
       ```cmd
       pyinstaller --onefile --add-data "inad_whatsapp.html;." --add-data "libs/*;libs" run.py
       ```
   *   **No macOS / Linux:**
       ```bash
       pyinstaller --onefile --add-data "inad_whatsapp.html:." --add-data "libs/*:libs" run.py
       ```
3. O executável pronto estará disponível dentro da pasta `dist/`.
