// Background Service Worker
chrome.runtime.onInstalled.addListener(() => {
  console.log("Gemini AI Chrome Copilot instalado com sucesso!");
});

// Listener for runtime messages (if needed in the future)
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Direct messages from content script to popup are handled directly by popup.js
  // But we can add logic here if background services are needed in the future.
  return true;
});
