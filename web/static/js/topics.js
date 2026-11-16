// The topic list of the page editor. "Add" and Enter add the typed topic to the
// list, "Remove" takes one out; nothing is saved until the form's Save. A topic
// already in the list, compared without case nor accents, is not added again.
const fold = text => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
const list = document.getElementById("topic-list");
const field = document.getElementById("topic-new");

function addTopic() {
  const topic = field.value.trim();
  const known = [...list.querySelectorAll("input[name=topics]")].map(input => fold(input.value));
  if (topic && !known.includes(fold(topic))) {
    const row = document.getElementById("topic-row").content.firstElementChild.cloneNode(true);
    row.querySelector("span").textContent = topic;
    row.querySelector("input").value = topic;
    list.append(row);
  }
  field.value = "";
  field.focus();
}

document.getElementById("topic-add").addEventListener("click", addTopic);
field.addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  e.preventDefault();  // Enter adds the topic; it does not submit the form
  addTopic();
});
list.addEventListener("click", e => {
  if (e.target.closest("[data-remove-topic]")) e.target.closest("li").remove();
});
