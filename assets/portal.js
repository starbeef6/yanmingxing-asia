(function () {
  var config = window.PORTAL_CONFIG;
  var page = document.querySelector("[data-page]");

  if (!config || !page) {
    return;
  }

  var icons = {
    shipping: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="m3.5 7.5 8.5 4.25 8.5-4.25M12 12v9" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
    table: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" stroke-width="1.7"/><path d="M3 9h18M9 9v11" stroke="currentColor" stroke-width="1.7"/></svg>',
    checklist: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2.5" stroke="currentColor" stroke-width="1.7"/><path d="m8 9 1.3 1.3L12 7.7M8 15l1.3 1.3L12 13.7M14 9h3M14 15h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    compare: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4v16M17 4v16M4 7l3-3 3 3M14 17l3 3 3-3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  };
  var chevron = '<svg class="choice-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m7.5 4.5 5 5.5-5 5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function getQuery(name) {
    return new URLSearchParams(window.location.search).get(name) || "";
  }

  function findDepartment(id) {
    return config.departments.find(function (department) {
      return department.id === id;
    });
  }

  function buildChoice(item, href, colors) {
    var link = document.createElement("a");
    var icon = document.createElement("span");
    var copy = document.createElement("span");
    var title = document.createElement("h2");
    var description = document.createElement("p");

    link.className = "choice-row";
    link.href = href;
    link.setAttribute("aria-label", "进入" + item.name);
    icon.className = "choice-icon";
    icon.style.setProperty("--icon-bg", colors.background);
    icon.style.setProperty("--icon-color", colors.color);
    icon.innerHTML = icons[item.icon] || icons.table;
    copy.className = "choice-copy";
    title.textContent = item.name;
    description.textContent = item.description || "";
    copy.appendChild(title);
    copy.appendChild(description);
    link.appendChild(icon);
    link.appendChild(copy);
    link.insertAdjacentHTML("beforeend", chevron);
    return link;
  }

  function renderDepartments() {
    var list = document.querySelector("[data-department-list]");
    if (!config.departments.length) {
      list.innerHTML = '<div class="empty-state">暂时没有可用部门。</div>';
      return;
    }
    config.departments.forEach(function (department) {
      list.appendChild(buildChoice(
        department,
        "department/?id=" + encodeURIComponent(department.id),
        { background: department.iconBackground || "#e8f2ff", color: department.iconColor || "#0071e3" }
      ));
    });
  }

  function renderTools() {
    var department = findDepartment(getQuery("id"));
    var list = document.querySelector("[data-tool-list]");
    var title = document.querySelector("[data-department-title]");
    var description = document.querySelector("[data-department-description]");

    if (!department) {
      title.textContent = "部门不存在";
      description.textContent = "请返回首页重新选择。";
      list.innerHTML = '<div class="error-state">没有找到这个部门。</div>';
      return;
    }

    document.title = department.name + " - 数据处理中心";
    title.textContent = department.name;
    description.textContent = "选择一个文件处理流程。";
    if (!department.tools.length) {
      list.innerHTML = '<div class="empty-state">这个部门暂时没有处理流程。</div>';
      return;
    }
    department.tools.forEach(function (tool) {
      list.appendChild(buildChoice(
        tool,
        "../tool/?department=" + encodeURIComponent(department.id) + "&id=" + encodeURIComponent(tool.id),
        { background: "#f2f2f7", color: "#515154" }
      ));
    });
  }

  if (page.getAttribute("data-page") === "departments") {
    renderDepartments();
  }
  if (page.getAttribute("data-page") === "tools") {
    renderTools();
  }
})();
