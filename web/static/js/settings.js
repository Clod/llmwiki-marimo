// "Restore the default" fills the system prompt field with the default prompt the
// textarea carries in `data-default`. Nothing is saved until the form is sent.
document.getElementById("restore-prompt")?.addEventListener("click", () => {
  const field = document.getElementById("f-system_prompt");
  field.value = field.dataset.default;
  field.focus();
});
