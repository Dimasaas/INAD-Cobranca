// Message Listener from Popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "GET_TEXT") {
    const pageText = getCleanPageText();
    sendResponse({ text: pageText });
  } 
  else if (request.action === "GET_STRUCTURE") {
    const structure = getPageStructureOutline();
    sendResponse({ structure: structure });
  }
  return true; // Keep message channel open for async response
});

// Extract text from the page, cleaning out script/style elements to save tokens
function getCleanPageText() {
  if (!document.body) return "";

  // Clone document body to avoid altering page
  const bodyClone = document.body.cloneNode(true);
  
  // Remove non-text elements
  const tagsToRemove = ['script', 'style', 'noscript', 'iframe', 'svg', 'path', 'canvas', 'video', 'audio'];
  tagsToRemove.forEach(tagName => {
    const elements = bodyClone.querySelectorAll(tagName);
    elements.forEach(el => el.remove());
  });
  
  // Get text content
  let text = bodyClone.innerText || bodyClone.textContent || "";
  
  // Clean up whitespace
  text = text.replace(/\s+/g, ' ').trim();
  
  // Max size check to prevent API failure
  const MAX_CHARACTERS = 500000;
  if (text.length > MAX_CHARACTERS) {
    text = text.substring(0, MAX_CHARACTERS) + "\n\n[Conteúdo truncado por exceder o limite de caracteres...]";
  }
  
  return text;
}

// Generate a compact outline of interactive DOM elements (buttons, inputs, selects)
function getPageStructureOutline() {
  if (!document.body) return "Página vazia.";

  const outline = [];
  
  // Selector for common interactive form elements
  const selectors = [
    'input[type="text"]', 'input[type="search"]', 'input[type="email"]', 'input[type="password"]',
    'input[type="number"]', 'input[type="submit"]', 'input[type="button"]', 'input[type="checkbox"]',
    'input[type="radio"]', 'textarea', 'select', 'button', 'a[href^="javascript"]',
    '.btn', '.button', '[role="button"]'
  ];
  
  const elements = document.querySelectorAll(selectors.join(', '));
  
  elements.forEach((el, index) => {
    const tagName = el.tagName.toLowerCase();
    const type = el.type || el.getAttribute('type') || '';
    const id = el.id ? `id="${el.id}"` : '';
    const name = el.name ? `name="${el.name}"` : '';
    const placeholder = el.getAttribute('placeholder') ? `placeholder="${el.getAttribute('placeholder')}"` : '';
    
    // Grab first 3 CSS classes to keep it concise
    const classes = el.className && typeof el.className === 'string' 
      ? `class="${el.className.split(' ').filter(Boolean).slice(0, 3).join(' ')}"` 
      : '';
      
    let extraDetails = '';
    if (tagName === 'button' || el.classList.contains('btn') || el.classList.contains('button')) {
      const btnText = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 40);
      extraDetails = btnText ? `label="${btnText}"` : '';
    } else if (tagName === 'select') {
      const options = Array.from(el.options).slice(0, 5).map(o => o.text.trim());
      extraDetails = `options=[${options.join(', ')}]`;
    } else if (tagName === 'input' && (type === 'checkbox' || type === 'radio')) {
      extraDetails = `checked=${el.checked}`;
    }

    const itemParts = [
      tagName.toUpperCase(),
      id,
      name,
      type ? `type="${type}"` : '',
      placeholder,
      classes,
      extraDetails
    ].filter(Boolean);
    
    outline.push(`${index + 1}. ${itemParts.join(' ')}`);
  });
  
  if (outline.length === 0) {
    return "Nenhum elemento interativo (botões ou inputs) detectado no corpo da página.";
  }
  
  // Return the first 80 interactive elements to avoid overwhelming context
  return outline.slice(0, 80).join('\n');
}
