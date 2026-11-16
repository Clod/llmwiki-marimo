// A notice that follows an action ("Deleted the page …") arrives in the URL (?notice=, ?deleted=).
// The parameter is removed from the address once the page shows it, so a reload or a
// bookmark does not show the notice again; the × of the notice closes it.
(() => {
  const url = new URL(location.href);
  let changed = false;
  // `n` and `error` are the values a notice carries (`web/notices.py`); they go with it.
  for (const name of ["notice", "deleted", "n", "error"]) {
    if (url.searchParams.has(name)) { url.searchParams.delete(name); changed = true; }
  }
  if (changed) history.replaceState(history.state, "", url.pathname + url.search + url.hash);
})();
