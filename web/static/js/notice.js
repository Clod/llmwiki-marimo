// A notice that follows an action ("Deleted the page …") arrives in the URL (?notice=, ?deleted=).
// The parameter is removed from the address once the page shows it, so a reload or a
// bookmark does not show the notice again; the × of the notice closes it.
(() => {
  const url = new URL(location.href);
  let changed = false;
  // `n`, `error`, `page` and `version` are the values a notice carries (`web/notices.py`);
  // they go with it, and only with it: the relations screen reads its own `page`.
  const values = url.searchParams.has("notice") ? ["n", "error", "page", "version"] : [];
  for (const name of ["notice", "deleted", ...values]) {
    if (url.searchParams.has(name)) { url.searchParams.delete(name); changed = true; }
  }
  if (changed) history.replaceState(history.state, "", url.pathname + url.search + url.hash);
})();
