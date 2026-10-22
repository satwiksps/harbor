// Keep the custom header keyboard accessible while using Material's toggles.
const drawer = document.getElementById("__drawer");
const menu = document.querySelector('[data-harbor-toggle="__drawer"]');
const sidebar = document.querySelector(".md-sidebar--primary");
const search = document.getElementById("__search");
const searchButton = document.querySelector('[data-harbor-toggle="__search"]');
const mobileNavigation = window.matchMedia("(max-width: 76.234375em)");

function syncDrawer() {
  const shouldHide = mobileNavigation.matches && !drawer.checked;
  const hadFocus = sidebar.contains(document.activeElement);
  sidebar.inert = shouldHide;
  if (shouldHide && hadFocus) menu.focus();
}
drawer.addEventListener("change", syncDrawer);
mobileNavigation.addEventListener("change", syncDrawer);
syncDrawer();

function currentPanel() {
  let panel = sidebar.querySelector(".md-nav--primary");
  while (true) {
    const child = [...panel.querySelectorAll(":scope > .md-nav__list > .md-nav__item > input:checked")]
      .map((toggle) => toggle.parentElement.querySelector(":scope > nav"))
      .find(Boolean);
    if (!child) return panel;
    panel = child;
  }
}

function panelControls() {
  const panel = currentPanel();
  return [...panel.querySelectorAll('a[href], button, [tabindex="0"]')]
    .filter((element) => element.closest("nav") === panel && element.getClientRects().length && getComputedStyle(element).visibility !== "hidden");
}

document.querySelector(".harbor-drawer-close").addEventListener("click", () => {
  if (drawer.checked) drawer.click();
  menu.focus();
});

document.querySelectorAll("[data-harbor-toggle]").forEach((button) => {
  const toggle = document.getElementById(button.dataset.harborToggle);
  const isMenu = toggle === drawer;
  const sync = () => {
    button.setAttribute("aria-expanded", String(toggle.checked));
    button.setAttribute("aria-label", `${toggle.checked ? "Close" : "Open"} ${isMenu ? "navigation" : "search"}`);
  };
  button.addEventListener("click", () => {
    toggle.click();
    if (isMenu && toggle.checked) {
      panelControls()[0]?.focus();
    } else if (!isMenu && toggle.checked) {
      document.querySelector(".md-search__input").focus();
    }
  });
  toggle.addEventListener("change", sync);
  sync();
});

// Material's nested drawer headings are labels rather than native controls.
const sectionControls = [...sidebar.querySelectorAll('.md-nav__link[for], .md-nav__title[for]:not([for="__drawer"])')];
function updateSectionControls() {
  sectionControls.forEach((control) => {
    const toggle = document.getElementById(control.htmlFor);
    if (mobileNavigation.matches) {
      control.tabIndex = 0;
      control.setAttribute("role", "button");
      control.setAttribute("aria-expanded", String(toggle.checked));
    } else {
      control.removeAttribute("tabindex");
      control.removeAttribute("role");
      control.removeAttribute("aria-expanded");
    }
  });
}
sectionControls.forEach((control) => {
  control.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      control.click();
    }
  });
});
new Set(sectionControls.map((control) => document.getElementById(control.htmlFor))).forEach((toggle) => {
  toggle.addEventListener("change", () => {
    updateSectionControls();
    if (!drawer.checked || !mobileNavigation.matches) return;
    const selector = toggle.checked ? ".md-nav__title" : ".md-nav__link";
    sectionControls.find((control) => control.htmlFor === toggle.id && control.matches(selector))?.focus();
  });
});
mobileNavigation.addEventListener("change", updateSectionControls);
updateSectionControls();

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && search.checked) {
    event.preventDefault();
    event.stopImmediatePropagation();
    search.click();
    (searchButton.getClientRects().length ? searchButton : document.querySelector(".harbor-wordmark")).focus();
    return;
  }
  if (!drawer.checked || !mobileNavigation.matches) return;
  if (event.key === "Escape") {
    event.preventDefault();
    drawer.click();
    menu.focus();
  } else if (event.key === "Tab") {
    const controls = panelControls();
    if (!controls.length) return;
    event.preventDefault();
    const index = controls.indexOf(document.activeElement);
    const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
    controls[next].focus();
  }
});

// Expose a keyboard scroll target only when a table or diagram needs one.
const regions = new Set();
const resizeObserver = new ResizeObserver(updateRegions);
function updateRegions() {
  document.querySelectorAll(".md-typeset__scrollwrap, .md-typeset .mermaid").forEach((region) => {
    if (!regions.has(region)) {
      regions.add(region);
      resizeObserver.observe(region);
    }
    const overflows = region.scrollWidth > region.clientWidth + 1;
    region.classList.toggle("harbor-scrollable", overflows);
    if (overflows) {
      region.tabIndex = 0;
      region.setAttribute("role", "region");
      region.setAttribute("aria-label", region.classList.contains("mermaid") ? "Architecture diagram, scroll horizontally" : "Table, scroll horizontally");
    } else {
      region.removeAttribute("tabindex");
      region.removeAttribute("role");
      region.removeAttribute("aria-label");
    }
  });
}
new MutationObserver(updateRegions).observe(document.querySelector(".md-content"), { childList: true, subtree: true });
updateRegions();
