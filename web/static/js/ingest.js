function toggle(id, open) { document.getElementById(id).hidden = !open; }
// The console stays in view when an operation starts.
document.body.addEventListener("htmx:afterSwap", e => {
  if (e.detail.target?.id === "progress") e.detail.target.scrollIntoView({ block: "nearest", behavior: "smooth" });
});

// The console follows the operation: each new line scrolls the log to its end, so the
// last line stays in view. A user who scrolls up in the log stops the following;
// scrolling back to the end resumes it, and a new operation (a new log) starts it again.
(() => {
  const progress = document.getElementById("progress");
  if (!progress) return;
  const SLACK = 24;                                   // px from the end that still count as "at the end"
  const following = new WeakMap();                    // log element → follows its end
  const atEnd = log => log.scrollHeight - log.scrollTop - log.clientHeight <= SLACK;
  new MutationObserver(() => {
    const log = progress.querySelector(".log");
    if (!log) return;
    if (!following.has(log)) {
      following.set(log, true);
      log.addEventListener("scroll", () => following.set(log, atEnd(log)), { passive: true });
    }
    if (following.get(log)) log.scrollTop = log.scrollHeight;
  }).observe(progress, { childList: true, subtree: true, characterData: true });
})();

// When an operation ends (its log receives "done"), the screen is told with the event
// "operation-done"; the sources panel of Ingestar listens to it and reloads itself.
document.body.addEventListener("htmx:sseClose", e => {
  if (e.detail?.type === "message" && e.target.closest?.("#progress")) htmx.trigger(document.body, "operation-done");
});
