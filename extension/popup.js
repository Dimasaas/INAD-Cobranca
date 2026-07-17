// State variables
let activeTabId = null;
let extractedPageText = "";
let uploadedFileText = "";
let uploadedFileName = "";
let inadContext = null; // Contexto estruturado do painel INAD (GET /api/context)

// DOM Elements
const apiStatusBadge = document.getElementById('api-status');
const navDoc = document.getElementById('btn-tab-doc');
const navAuto = document.getElementById('btn-tab-auto');
const navSettings = document.getElementById('btn-tab-settings');

const panelDoc = document.getElementById('panel-doc');
const panelAuto = document.getElementById('panel-auto');
const panelSettings = document.getElementById('panel-settings');

const docPromptInput = document.getElementById('doc-prompt');
const autoPromptInput = document.getElementById('auto-prompt');
const apiKeyInput = document.getElementById('input-api-key');
const modelSelect = document.getElementById('select-model');

const btnReadPage = document.getElementById('btn-read-page');
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileInfoBox = document.getElementById('file-info');
const selectedFileName = document.getElementById('selected-file-name');
const btnRemoveFile = document.getElementById('btn-remove-file');

const btnRunDoc = document.getElementById('btn-run-doc');
const btnRunAuto = document.getElementById('btn-run-auto');
const btnSaveSettings = document.getElementById('btn-save-settings');
const btnToggleKey = document.getElementById('btn-toggle-key');

const consoleLogs = document.getElementById('console-logs');
const btnClearConsole = document.getElementById('btn-clear-console');

const outputArea = document.getElementById('output-area');
const outputContent = document.getElementById('output-content');
const btnCopyOutput = document.getElementById('btn-copy-output');

// Initialize Extension
document.addEventListener('DOMContentLoaded', async () => {
  // Load saved settings
  const settings = await chrome.storage.local.get(['apiKey', 'model']);
  if (settings.apiKey) {
    apiKeyInput.value = settings.apiKey;
    updateApiStatus(true);
  } else {
    updateApiStatus(false);
  }
  
  if (settings.model) {
    modelSelect.value = settings.model;
  }

  // Setup tab event listeners
  setupTabs();
  
  // Setup file upload listeners
  setupFileUpload();

  // Setup other action listeners
  setupActions();
  
  // Listen for logs from content script
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === 'AUTO_LOG') {
      logToConsole(message.text, message.level || 'info');
    }
  });
});

// Update API Status Badge
function updateApiStatus(connected) {
  if (connected) {
    apiStatusBadge.textContent = "API Conectada";
    apiStatusBadge.className = "status-badge status-active";
  } else {
    apiStatusBadge.textContent = "Sem Chave API";
    apiStatusBadge.className = "status-badge status-missing";
  }
}

// Console logger helper
function logToConsole(text, level = 'info') {
  const line = document.createElement('div');
  line.className = `log-line log-${level}`;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
  consoleLogs.appendChild(line);
  consoleLogs.scrollTop = consoleLogs.scrollHeight;
}

// Tab Switching logic
function setupTabs() {
  const tabs = [
    { button: navDoc, panel: panelDoc },
    { button: navAuto, panel: panelAuto },
    { button: navSettings, panel: panelSettings }
  ];

  tabs.forEach(tab => {
    tab.button.addEventListener('click', () => {
      // Deactivate all
      tabs.forEach(t => {
        t.button.classList.remove('active');
        t.panel.classList.remove('active');
      });
      // Activate clicked
      tab.button.classList.add('active');
      tab.panel.classList.add('active');
      
      // Hide output if switching to settings
      if (tab.panel === panelSettings) {
        outputArea.style.display = 'none';
      }
    });
  });
}

// File Upload Drag & Drop logic
function setupFileUpload() {
  dropZone.addEventListener('click', () => fileInput.click());

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      handleFile(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      handleFile(e.target.files[0]);
    }
  });

  btnRemoveFile.addEventListener('click', () => {
    uploadedFileText = "";
    uploadedFileName = "";
    fileInput.value = "";
    fileInfoBox.style.display = 'none';
    dropZone.style.display = 'flex';
  });
}

function handleFile(file) {
  uploadedFileName = file.name;
  
  // Simple check for text files
  const allowedExtensions = ['txt', 'json', 'csv', 'html', 'js', 'md', 'xml', 'css'];
  const extension = file.name.split('.').pop().toLowerCase();
  
  if (extension === 'pdf') {
    // Alert user about PDF limitations locally
    alert("Dica de Performance: PDFs complexos com imagens são pesados para ler puramente em JavaScript local. \n\nPara um melhor resultado, abra o PDF em uma aba do Chrome e clique no botão 'Ler Aba Atual'!");
  }

  const reader = new FileReader();
  reader.onload = (e) => {
    uploadedFileText = e.target.result;
    selectedFileName.textContent = `${file.name} (${formatBytes(file.size)})`;
    dropZone.style.display = 'none';
    fileInfoBox.style.display = 'flex';
  };
  
  reader.onerror = () => {
    alert("Erro ao ler o arquivo selecionado.");
  };

  reader.readAsText(file);
}

function formatBytes(bytes, decimals = 2) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Detecta se a aba ativa é o painel INAD e busca contexto estruturado da API
async function fetchINADContext(tabUrl) {
  if (!tabUrl) return null;
  try {
    const url = new URL(tabUrl);
    const isLocal = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
    const isINAD = url.pathname.includes('inad_whatsapp') || url.pathname.includes('inad_template');
    if (!isLocal || !isINAD) return null;

    const contextUrl = `${url.origin}/api/context`;
    const res = await fetch(contextUrl);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

function buildINADContextBlock(ctx) {
  if (!ctx) return "";
  const stats = ctx.live_stats || {};
  const endpoints = Object.keys(ctx.api_endpoints || {}).slice(0, 12).join(", ");
  return `
=== CONTEXTO DO SISTEMA INAD (Painel de Cobrança) ===
Projeto: ${ctx.project?.name || "INAD"}
Propósito: ${ctx.project?.purpose || ""}
Relatórios no banco: ${stats.reports ?? "—"} | Clientes únicos: ${stats.unique_clients ?? "—"} | Contatados: ${stats.clients_contacted ?? "—"}

Regras de negócio:
${(ctx.business_rules ? Object.entries(ctx.business_rules).map(([k, v]) => `- ${k}: ${v}`).join("\n") : "")}

Endpoints principais: ${endpoints}

Diretrizes para I.A.:
${(ctx.ai_guidelines || []).map(g => `- ${g}`).join("\n")}
=== FIM DO CONTEXTO INAD ===
`;
}

// General Actions Configuration
function setupActions() {
  // Toggle password visibility
  btnToggleKey.addEventListener('click', () => {
    if (apiKeyInput.type === 'password') {
      apiKeyInput.type = 'text';
      btnToggleKey.textContent = '🔒';
    } else {
      apiKeyInput.type = 'password';
      btnToggleKey.textContent = '👁️';
    }
  });

  // Save Settings
  btnSaveSettings.addEventListener('click', async () => {
    const key = apiKeyInput.value.trim();
    const model = modelSelect.value;
    
    await chrome.storage.local.set({ apiKey: key, model: model });
    updateApiStatus(!!key);
    alert("Configurações salvas com sucesso!");
  });

  // Clear Console
  btnClearConsole.addEventListener('click', () => {
    consoleLogs.innerHTML = '<div class="log-line log-system">Console limpo.</div>';
  });

  // Copy Output
  btnCopyOutput.addEventListener('click', () => {
    navigator.clipboard.writeText(outputContent.textContent);
    btnCopyOutput.textContent = "Copiado!";
    setTimeout(() => {
      btnCopyOutput.textContent = "Copiar";
    }, 2000);
  });

  // Chip buttons helper
  document.querySelectorAll('.chip-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      docPromptInput.value = btn.dataset.prompt;
    });
  });

  // Read Current Page Text
  btnReadPage.addEventListener('click', async () => {
    try {
      btnReadPage.disabled = true;
      btnReadPage.textContent = "Lendo...";
      
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        throw new Error("Nenhuma aba ativa encontrada.");
      }

      // Inject content script if not already there
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['content.js']
        });
      } catch (err) {
        // Content script might be preloaded or injected already, or Chrome system page (chrome://)
        console.warn("Script injection skipped or page is restricted:", err);
      }

      // Request text from content script
      chrome.tabs.sendMessage(tab.id, { action: "GET_TEXT" }, async (response) => {
        btnReadPage.disabled = false;
        btnReadPage.innerHTML = `<span class="btn-icon">📄</span> Ler Aba Atual`;

        if (chrome.runtime.lastError) {
          alert("Não foi possível ler esta página. Certifique-se de que não é uma aba interna do Chrome (chrome://) ou a Chrome Web Store.");
          return;
        }

        if (response && response.text) {
          extractedPageText = response.text;
          uploadedFileText = "";
          fileInfoBox.style.display = 'none';
          dropZone.style.display = 'flex';

          inadContext = await fetchINADContext(tab.url);
          const ctxNote = inadContext ? " Contexto INAD carregado da API." : "";
          
          alert(`Conteúdo da aba "${tab.title.substring(0, 30)}..." carregado com sucesso (${formatBytes(extractedPageText.length * 2)})!${ctxNote}`);
        } else {
          alert("A página retornou um texto vazio.");
        }
      });
    } catch (error) {
      btnReadPage.disabled = false;
      btnReadPage.innerHTML = `<span class="btn-icon">📄</span> Ler Aba Atual`;
      alert("Erro ao ler página: " + error.message);
    }
  });

  // Run Document Analysis
  btnRunDoc.addEventListener('click', async () => {
    const key = apiKeyInput.value.trim();
    if (!key) {
      alert("Por favor, configure sua Gemini API Key na aba Configurações.");
      return;
    }

    const textToAnalyze = uploadedFileText || extractedPageText;
    if (!textToAnalyze) {
      alert("Por favor, primeiro faça upload de um arquivo ou clique em 'Ler Aba Atual' para carregar algum conteúdo.");
      return;
    }

    const instruction = docPromptInput.value.trim();
    if (!instruction) {
      alert("Por favor, digite uma instrução ou pergunta sobre o documento.");
      return;
    }

    btnRunDoc.disabled = true;
    btnRunDoc.textContent = "Pensando...";
    outputArea.style.display = 'none';

    try {
      const model = modelSelect.value;
      const contextBlock = buildINADContextBlock(inadContext);
      const responseText = await callGeminiAPI(key, model, textToAnalyze, instruction, contextBlock);
      
      outputContent.textContent = responseText;
      outputArea.style.display = 'block';
      outputArea.scrollIntoView({ behavior: 'smooth' });
    } catch (error) {
      alert("Erro na análise: " + error.message);
    } finally {
      btnRunDoc.disabled = false;
      btnRunDoc.textContent = "Analisar com Gemini";
    }
  });

  // Run Chrome Automation
  btnRunAuto.addEventListener('click', async () => {
    const key = apiKeyInput.value.trim();
    if (!key) {
      alert("Por favor, configure sua Gemini API Key na aba Configurações.");
      return;
    }

    const instruction = autoPromptInput.value.trim();
    if (!instruction) {
      alert("Por favor, descreva qual tarefa você quer automatizar.");
      return;
    }

    btnRunAuto.disabled = true;
    btnRunAuto.textContent = "Gerando Automação...";
    logToConsole("Iniciando requisição de automação...", "system");

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        throw new Error("Nenhuma aba ativa encontrada.");
      }

      // Check if it's a chrome:// tab which is restricted
      if (tab.url.startsWith('chrome://') || tab.url.startsWith('edge://') || tab.url.startsWith('about:')) {
        throw new Error("Não é possível automatizar páginas do sistema Chrome (chrome://). Abra um site comum.");
      }

      // 1. Get HTML Page outline to help LLM write exact selectors
      logToConsole("Lendo estrutura da página para a I.A...", "info");
      
      // Inject content script if needed
      try {
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      } catch(e) {}

      const pageStructure = await getPageStructure(tab.id);

      // Contexto INAD quando a aba é o painel de cobrança
      inadContext = await fetchINADContext(tab.url);
      const contextBlock = buildINADContextBlock(inadContext);
      
      logToConsole("Solicitando código de automação ao Gemini...", "info");
      const model = modelSelect.value;
      const generatedCode = await callGeminiForAutomation(key, model, pageStructure, instruction, contextBlock);
      
      logToConsole("Código JavaScript gerado com sucesso pela I.A.", "success");
      logToConsole("Injetando script na página...", "info");

      // 2. Execute script
      await executeAutomationOnTab(tab.id, generatedCode);
      
    } catch (error) {
      logToConsole(`Erro: ${error.message}`, "error");
      alert("Erro na automação: " + error.message);
    } finally {
      btnRunAuto.disabled = false;
      btnRunAuto.textContent = "Gerar & Executar Automação";
    }
  });
}

// Request structure from Content Script
function getPageStructure(tabId) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, { action: "GET_STRUCTURE" }, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error("Erro de comunicação com a aba ativa. Atualize a página e tente novamente."));
      } else if (response && response.structure) {
        resolve(response.structure);
      } else {
        resolve("Não foi possível ler a estrutura HTML (DOM vazio ou inacessível).");
      }
    });
  });
}

// Call Gemini for Document Analysis
async function callGeminiAPI(key, model, documentText, prompt, systemContext = "") {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${key}`;
  
  const payload = {
    contents: [{
      parts: [{
        text: `Você é o Gemini AI Copilot integrado diretamente no navegador do usuário.
${systemContext}
Você está analisando o seguinte documento/texto fornecido:

=== INÍCIO DO DOCUMENTO ===
${documentText}
=== FIM DO DOCUMENTO ===

Por favor, execute a seguinte instrução com base no documento acima. Responda em Português de forma direta, clara e formatada de maneira limpa:
${prompt}`
      }]
    }]
  };

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    const message = errData.error?.message || response.statusText;
    throw new Error(`API erro (${response.status}): ${message}`);
  }

  const data = await response.json();
  return data.candidates?.[0]?.content?.parts?.[0]?.text || "Sem resposta do modelo.";
}

// Call Gemini to generate JavaScript automation
async function callGeminiForAutomation(key, model, pageStructure, userInstruction, systemContext = "") {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${key}`;
  
  const prompt = `Você é um robô gerador de scripts de automação de navegador (Browser Automation Generator).
Sua tarefa é gerar uma função auto-executável em JavaScript (IIFE) para rodar na página web ativa do usuário para executar uma ação.
${systemContext}
Abaixo está o resumo da estrutura HTML (inputs, botões e links relevantes) da página ativa:
---
${pageStructure}
---

O usuário quer fazer a seguinte automação:
"${userInstruction}"

Instruções críticas para geração do código:
1. Retorne APENAS o código JavaScript puro pronto para ser executado.
2. NÃO use blocos de marcação markdown como \`\`\`js ou \`\`\`. O retorno deve ser texto limpo.
3. Crie uma IIFE assíncrona: (async () => { ... })();
4. Use seletores precisos e trate erros se os elementos não existirem.
5. Para reportar logs do progresso da automação de volta para a extensão, use a função:
   chrome.runtime.sendMessage({ type: "AUTO_LOG", text: "Mensagem do log...", level: "info" | "success" | "error" | "warning" });
   *Use essa função no início, fim e durante as etapas principais da automação!*
6. Execute scrolls se necessário, simule eventos de clique ou preenchimento de input com segurança.
7. Evite usar loops infinitos ou esperas excessivas. Se precisar esperar o carregamento de algo, use uma função de timeout curta.

Exemplo de estrutura esperada:
(async () => {
  chrome.runtime.sendMessage({ type: "AUTO_LOG", text: "Iniciando automação...", level: "info" });
  try {
    const input = document.querySelector('input[type="search"], input[name="q"]');
    if (!input) throw new Error("Campo de busca não encontrado.");
    input.value = "Alguma pesquisa";
    input.dispatchEvent(new Event('input', { bubbles: true }));
    chrome.runtime.sendMessage({ type: "AUTO_LOG", text: "Termo de busca preenchido.", level: "info" });
    
    const btn = document.querySelector('button[type="submit"], input[type="submit"]');
    if (!btn) throw new Error("Botão de submit não encontrado.");
    btn.click();
    chrome.runtime.sendMessage({ type: "AUTO_LOG", text: "Formulário enviado!", level: "success" });
  } catch (err) {
    chrome.runtime.sendMessage({ type: "AUTO_LOG", text: "Falha: " + err.message, level: "error" });
  }
})();`;

  const payload = {
    contents: [{
      parts: [{ text: prompt }]
    }]
  };

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    const message = errData.error?.message || response.statusText;
    throw new Error(`API erro (${response.status}): ${message}`);
  }

  const data = await response.json();
  let jsCode = data.candidates?.[0]?.content?.parts?.[0]?.text || "";
  
  // Clean up code if Gemini still returned markdown wraps despite instructions
  jsCode = jsCode.replace(/```javascript/g, '').replace(/```js/g, '').replace(/```/g, '').trim();
  
  return jsCode;
}

// Injects dynamic script on the active page tab
async function executeAutomationOnTab(tabId, codeString) {
  function injectUserScript(scriptBody) {
    const script = document.createElement('script');
    script.textContent = scriptBody;
    (document.head || document.documentElement).appendChild(script);
    script.remove();
  }

  await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: injectUserScript,
    args: [codeString]
  });
}
