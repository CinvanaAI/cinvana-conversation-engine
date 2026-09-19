const mermaid = globalThis.mermaid;
import { diagrams } from "./diagrams.js";

const el = {};
for (const id of ["categories","list","search","previous","next","category","title","position","diagram","source","source-toggle","loading","error"]) {
  el[id] = document.getElementById(id);
}
const state = {
  category: "All",
  query: "",
  selected: location.hash.slice(1) || "00",
  source: false,
};

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "base",
  flowchart: { htmlLabels: true, curve: "basis", useMaxWidth: false },
  sequence: { useMaxWidth: false, wrap: true, diagramMarginX: 24 },
  themeVariables: {
    fontFamily: "Inter, Segoe UI, sans-serif",
    fontSize: "14px",
    primaryColor: "#e5f4f2",
    primaryTextColor: "#182024",
    primaryBorderColor: "#087f79",
    secondaryColor: "#fff3db",
    tertiaryColor: "#eef1f3",
    lineColor: "#66767d",
    clusterBkg: "#f7f9fa",
    clusterBorder: "#9daab0",
    noteBkgColor: "#fff3db",
    noteBorderColor: "#b16b0b",
    actorBkg: "#e5f4f2",
    actorBorder: "#087f79",
    signalColor: "#47565d",
  },
});

const current = () => diagrams.find((item) => item.id === state.selected) || diagrams[0];
const visible = () => {
  const query = state.query.trim().toLowerCase();
  return diagrams.filter((item) =>
    (state.category === "All" || item.category === state.category) &&
    (!query || (item.id + " " + item.title + " " + item.category).toLowerCase().includes(query))
  );
};

function renderCategories() {
  const names = ["All", ...new Set(diagrams.map((item) => item.category))];
  el.categories.replaceChildren(...names.map((name) => {
    const button = document.createElement("button");
    button.className = "category-button" + (state.category === name ? " active" : "");
    button.textContent = name;
    button.onclick = () => {
      state.category = name;
      renderCategories();
      renderList();
    };
    return button;
  }));
}

function renderList() {
  const items = visible();
  if (!items.length) {
    const message = document.createElement("p");
    message.textContent = "No matching diagrams.";
    el.list.replaceChildren(message);
    return;
  }
  el.list.replaceChildren(...items.map((item) => {
    const button = document.createElement("button");
    button.className = "diagram-button" + (state.selected === item.id ? " active" : "");
    const number = document.createElement("span");
    number.className = "number";
    number.textContent = item.id;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.title;
    button.append(number, name);
    button.onclick = () => select(item.id);
    return button;
  }));
}

async function renderCurrent() {
  const item = current();
  const index = diagrams.indexOf(item);
  state.selected = item.id;
  el.category.textContent = item.category;
  el.title.textContent = item.title;
  el.position.textContent = (index + 1) + " / " + diagrams.length;
  el.previous.disabled = index === 0;
  el.next.disabled = index === diagrams.length - 1;
  el.source.textContent = item.code.trim();
  el.source.hidden = !state.source;
  el.diagram.hidden = state.source;
  el.loading.hidden = state.source;
  el.error.hidden = true;
  renderList();
  if (state.source) return;
  el.diagram.replaceChildren();
  el.loading.hidden = false;
  try {
    const result = await mermaid.render("ce-" + item.id + "-" + Date.now(), item.code.trim());
    el.diagram.innerHTML = result.svg;
    if (result.bindFunctions) result.bindFunctions(el.diagram);
    el.loading.hidden = true;
  } catch (error) {
    el.loading.hidden = true;
    el.error.textContent = "Diagram " + item.id + " could not render.\n\n" + String(error);
    el.error.hidden = false;
  }
}

function select(id) {
  state.selected = id;
  history.replaceState(null, "", "#" + id);
  renderCurrent();
}
el.search.oninput = (event) => {
  state.query = event.target.value;
  renderList();
};
el.previous.onclick = () => {
  const index = diagrams.indexOf(current());
  if (index > 0) select(diagrams[index - 1].id);
};
el.next.onclick = () => {
  const index = diagrams.indexOf(current());
  if (index < diagrams.length - 1) select(diagrams[index + 1].id);
};
el["source-toggle"].onclick = () => {
  state.source = !state.source;
  el["source-toggle"].setAttribute("aria-pressed", String(state.source));
  renderCurrent();
};
document.onkeydown = (event) => {
  if (event.target instanceof HTMLInputElement) return;
  if (event.key === "ArrowLeft") el.previous.click();
  if (event.key === "ArrowRight") el.next.click();
};

renderCategories();
renderList();
renderCurrent();
