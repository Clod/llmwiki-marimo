// "Restore the default" fills the system prompt field with the default prompt the
// textarea carries in `data-default`. Nothing is saved until the form is sent.
document.getElementById("restore-prompt")?.addEventListener("click", () => {
  const field = document.getElementById("f-system_prompt");
  field.value = field.dataset.default;
  field.focus();
});

// The models form has no action: each group sends its fields with its own buttons
// (htmx). A submission of the form itself, by the Enter key, is cancelled.
document.querySelector(".models-form")?.addEventListener("submit", (event) => event.preventDefault());

// The "?" button of a group opens the dialog named in `data-help`. The "Close" button
// closes it. A modal dialog also closes with the Escape key.
document.querySelectorAll("[data-help]").forEach((button) => {
  button.addEventListener("click", () => document.getElementById(button.dataset.help)?.showModal());
});
document.querySelectorAll("dialog.help [data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});
