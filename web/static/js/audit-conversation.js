// The audit page opens in a new tab, and a new tab starts with an empty sessionStorage.
// The page records its conversation under the key that the chat reads (reader.js), so
// that the link «Read» of this tab shows the same conversation, not an empty chat.
(() => {
  const main = document.querySelector(".audit-columns");
  if (!main?.dataset.wiki || !main.dataset.conversation) return;
  try { sessionStorage.setItem("llmwiki.conversation." + main.dataset.wiki, main.dataset.conversation); } catch {}
})();
