(function () {
  "use strict";

  var baseurl = (window.APP_CONFIG && window.APP_CONFIG.baseurl) || "";

  // === State ===
  var state = {
    allData: [],
    filteredData: [],
    currentPage: 1,
    pageSize: 20,
    filters: [],    // array of { type: "tag"|"text", value: string }
    editMode: false
  };

  // === DOM refs ===
  var searchInput = document.getElementById("search-input");
  var searchBtn = document.getElementById("search-btn");
  var tableBody = document.getElementById("table-body");
  var paginationTop = document.getElementById("pagination-top");
  var paginationBottom = document.getElementById("pagination-bottom");
  var activeFilters = document.getElementById("active-filters");
  var btnEdit = document.getElementById("btn-edit");
  var btnSave = document.getElementById("btn-save");
  var btnAdd = document.getElementById("btn-add");
  var btnCancel = document.getElementById("btn-cancel");
  var actionsHeaders = document.querySelectorAll(".col-actions");

  // === ISO country code to flag emoji ===
  function countryFlag(code) {
    if (!code) return "";
    var upper = code.toUpperCase();
    var cp1 = 0x1F1E6 - 65 + upper.charCodeAt(0);
    var cp2 = 0x1F1E6 - 65 + upper.charCodeAt(1);
    return String.fromCodePoint(cp1, cp2);
  }

  // === Escape HTML ===
  function esc(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  // === URL Management ===
  function readURL() {
    var params = new URLSearchParams(window.location.search);
    var tagsParam = params.get("tags");
    var textParam = params.get("text");

    state.filters = [];

    if (tagsParam) {
      tagsParam.split(",").map(function (t) { return t.trim(); }).filter(Boolean).forEach(function (t) {
        state.filters.push({ type: "tag", value: t });
      });
    }

    if (textParam) {
      state.filters.push({ type: "text", value: textParam });
      searchInput.value = textParam;
    } else {
      searchInput.value = "";
    }
  }

  function syncURL() {
    var params = new URLSearchParams();

    var tags = state.filters
      .filter(function (f) { return f.type === "tag"; })
      .map(function (f) { return f.value; });
    if (tags.length) {
      params.set("tags", tags.join(","));
    }

    var textFilter = state.filters.find(function (f) { return f.type === "text"; });
    if (textFilter) {
      params.set("text", textFilter.value);
    }

    var qs = params.toString();
    var newURL = window.location.pathname + (qs ? "?" + qs : "");
    history.pushState(null, "", newURL);
  }

  // === Init ===
  function init() {
    fetch(baseurl + "/raw/data.json")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.allData = data;
        readURL();
        applySearch();
      })
      .catch(function (err) {
        console.error("Failed to load data:", err);
        tableBody.innerHTML = '<tr><td colspan="6">Failed to load data.</td></tr>';
      });

    bindEvents();
  }

  // === Render ===
  function render() {
    renderTable();
    renderPagination();
    renderFilters();
  }

  function renderTable() {
    var start = (state.currentPage - 1) * state.pageSize;
    var end = start + state.pageSize;
    var page = state.filteredData.slice(start, end);

    if (page.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="6">No results found.</td></tr>';
      return;
    }

    var html = "";
    for (var i = 0; i < page.length; i++) {
      var item = page[i];
      var globalIndex = start + i;
      var tagsHtml = "";
      if (item.Tags && item.Tags.length) {
        for (var t = 0; t < item.Tags.length; t++) {
          var tagName = item.Tags[t];
          var tagHref = "?tags=" + encodeURIComponent(tagName);
          tagsHtml += '<a class="tag" href="' + esc(tagHref) + '" data-tag="' + esc(tagName) + '">' + esc(tagName) + "</a>";
        }
      }

      html += "<tr data-index=\"" + globalIndex + "\">";
      html += "<td><a href=\"" + esc(item.Link) + "\" target=\"_blank\" rel=\"noopener\">" + esc(item.Title) + "</a></td>";
      html += "<td>" + esc(item.Author) + "</td>";
      html += "<td>" + esc(item.Type) + "</td>";
      html += "<td>" + countryFlag(item.Language) + " " + esc(item.Language) + "</td>";
      html += "<td>" + tagsHtml + "</td>";
      html += '<td class="col-actions' + (state.editMode ? "" : " hidden") + '">';
      html += '<div class="row-actions">';
      html += '<button class="btn-row-edit" data-index="' + globalIndex + '">Edit</button>';
      html += '<button class="btn-delete" data-index="' + globalIndex + '">Delete</button>';
      html += "</div></td>";
      html += "</tr>";
    }

    tableBody.innerHTML = html;
  }

  function renderPagination() {
    var totalPages = Math.max(1, Math.ceil(state.filteredData.length / state.pageSize));
    var html = "";

    html += '<button class="pg-prev"' + (state.currentPage <= 1 ? " disabled" : "") + '>Prev</button>';

    for (var p = 1; p <= totalPages; p++) {
      html += '<button class="pg-num' + (p === state.currentPage ? " active" : "") + '" data-page="' + p + '">' + p + "</button>";
    }

    html += '<button class="pg-next"' + (state.currentPage >= totalPages ? " disabled" : "") + '>Next</button>';

    paginationTop.innerHTML = html;
    paginationBottom.innerHTML = html;
  }

  // === Filter Chips ===
  function renderFilters() {
    if (state.filters.length === 0) {
      activeFilters.innerHTML = "";
      return;
    }

    var html = "";
    for (var i = 0; i < state.filters.length; i++) {
      var f = state.filters[i];
      var label = f.type === "tag" ? f.value : f.value;
      var prefix = f.type === "tag" ? "tag: " : "";
      html += '<span class="filter-chip" data-filter-type="' + esc(f.type) + '">'
        + '<span class="chip-label">' + esc(prefix) + esc(label) + '</span>'
        + '<button class="chip-remove" data-type="' + esc(f.type) + '" data-value="' + esc(f.value) + '" aria-label="Remove ' + esc(f.value) + '">&times;</button>'
        + '</span>';
    }

    activeFilters.innerHTML = html;
  }

  function removeFilter(type, value) {
    state.filters = state.filters.filter(function (f) {
      if (f.type !== type) return true;
      if (type === "tag") return f.value.toLowerCase() !== value.toLowerCase();
      return false; // remove text filter
    });

    if (type === "text") {
      searchInput.value = "";
    }

    syncURL();
    applySearch();
  }

  // === Search ===
  function applySearch() {
    state.filteredData = state.allData.slice();

    for (var i = 0; i < state.filters.length; i++) {
      var f = state.filters[i];
      if (f.type === "tag") {
        var tagLower = f.value.toLowerCase();
        state.filteredData = state.filteredData.filter(function (item) {
          var itemTags = (item.Tags || []).map(function (t) { return t.toLowerCase(); });
          return itemTags.indexOf(tagLower) !== -1;
        });
      } else if (f.type === "text") {
        var term = f.value.toLowerCase().trim();
        state.filteredData = state.filteredData.filter(function (item) {
          var text = [
            item.Title,
            item.Author,
            item.Type,
            item.Language,
            (item.Tags || []).join(" ")
          ].join(" ").toLowerCase();
          return text.indexOf(term) !== -1;
        });
      }
    }

    state.currentPage = 1;
    render();
  }

  // === Tag Click ===
  function handleTagClick(tagName) {
    // Check if this tag filter already exists
    var exists = state.filters.some(function (f) {
      return f.type === "tag" && f.value.toLowerCase() === tagName.toLowerCase();
    });
    if (!exists) {
      state.filters.push({ type: "tag", value: tagName });
    }
    searchInput.value = "";
    syncURL();
    applySearch();
  }

  // === Text Search ===
  function handleTextSearch() {
    var val = searchInput.value.trim();

    // Remove existing text filter
    state.filters = state.filters.filter(function (f) { return f.type !== "text"; });

    // Add new one if non-empty
    if (val) {
      state.filters.push({ type: "text", value: val });
    }

    syncURL();
    applySearch();
  }

  // === Edit Mode ===
  function enterEditMode() {
    state.editMode = true;
    btnEdit.classList.add("hidden");
    btnSave.classList.remove("hidden");
    btnAdd.classList.remove("hidden");
    btnCancel.classList.remove("hidden");
    for (var i = 0; i < actionsHeaders.length; i++) {
      actionsHeaders[i].classList.remove("hidden");
    }
    var cells = tableBody.querySelectorAll(".col-actions");
    for (var j = 0; j < cells.length; j++) {
      cells[j].classList.remove("hidden");
    }
    render();
  }

  function exitEditMode() {
    state.editMode = false;
    btnEdit.classList.remove("hidden");
    btnSave.classList.add("hidden");
    btnAdd.classList.add("hidden");
    btnCancel.classList.add("hidden");
    for (var i = 0; i < actionsHeaders.length; i++) {
      actionsHeaders[i].classList.add("hidden");
    }
    render();
  }

  // === Row Operations ===
  function editRow(index) {
    var item = state.filteredData[index];
    if (!item) return;

    var row = tableBody.querySelector('tr[data-index="' + index + '"]');
    if (!row) return;

    var cells = row.children;

    cells[0].innerHTML = '<input type="text" class="edit-title" value="' + esc(item.Title) + '">'
      + '<input type="text" class="edit-link" value="' + esc(item.Link) + '" placeholder="URL">';
    cells[1].innerHTML = '<input type="text" class="edit-author" value="' + esc(item.Author) + '">';
    cells[2].innerHTML = '<select class="edit-type">'
      + '<option value="article"' + (item.Type === "article" ? " selected" : "") + '>article</option>'
      + '<option value="video"' + (item.Type === "video" ? " selected" : "") + '>video</option>'
      + '<option value="site"' + (item.Type === "site" ? " selected" : "") + '>site</option>'
      + "</select>";
    cells[3].innerHTML = '<input type="text" class="edit-language" value="' + esc(item.Language) + '" placeholder="ISO code" style="width:60px">';
    cells[4].innerHTML = '<input type="text" class="edit-tags" value="' + esc((item.Tags || []).join(", ")) + '" placeholder="comma separated">';

    cells[5].innerHTML = '<div class="row-actions">'
      + '<button class="btn-row-save" data-index="' + index + '">Save</button>'
      + '<button class="btn-row-cancel" data-index="' + index + '">Cancel</button>'
      + "</div>";
  }

  function saveRow(index) {
    var row = tableBody.querySelector('tr[data-index="' + index + '"]');
    if (!row) return;

    var title = row.querySelector(".edit-title");
    if (!title) return;

    var item = state.filteredData[index];
    item.Title = title.value.trim();
    item.Link = row.querySelector(".edit-link").value.trim();
    item.Author = row.querySelector(".edit-author").value.trim();
    item.Type = row.querySelector(".edit-type").value;
    item.Language = row.querySelector(".edit-language").value.trim();

    var tagsStr = row.querySelector(".edit-tags").value;
    item.Tags = tagsStr.split(",").map(function (t) { return t.trim(); }).filter(Boolean);

    render();
  }

  function addRow() {
    var newItem = {
      Link: "",
      Title: "New Link",
      Author: "",
      Type: "article",
      Language: "us",
      Tags: []
    };
    state.allData.unshift(newItem);
    applySearch();
    editRow(0);
  }

  function deleteRow(index) {
    var item = state.filteredData[index];
    if (!item) return;
    if (!confirm('Delete "' + item.Title + '"?')) return;

    var allIdx = state.allData.indexOf(item);
    if (allIdx !== -1) state.allData.splice(allIdx, 1);

    applySearch();
  }

  // === Save JSON ===
  function saveJSON() {
    var json = JSON.stringify(state.allData, null, 2);
    var blob = new Blob([json + "\n"], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "data.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // === Event Binding ===
  function bindEvents() {
    // Search — text mode
    searchBtn.addEventListener("click", handleTextSearch);

    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        handleTextSearch();
      }
    });

    // Toolbar
    btnEdit.addEventListener("click", enterEditMode);
    btnCancel.addEventListener("click", exitEditMode);
    btnSave.addEventListener("click", saveJSON);
    btnAdd.addEventListener("click", addRow);

    // Pagination (event delegation)
    function handlePaginationClick(e) {
      var btn = e.target;
      if (btn.tagName !== "BUTTON" || btn.disabled) return;

      var totalPages = Math.max(1, Math.ceil(state.filteredData.length / state.pageSize));

      if (btn.classList.contains("pg-prev")) {
        if (state.currentPage > 1) {
          state.currentPage--;
          render();
        }
      } else if (btn.classList.contains("pg-next")) {
        if (state.currentPage < totalPages) {
          state.currentPage++;
          render();
        }
      } else if (btn.dataset.page) {
        state.currentPage = parseInt(btn.dataset.page, 10);
        render();
      }
    }

    paginationTop.addEventListener("click", handlePaginationClick);
    paginationBottom.addEventListener("click", handlePaginationClick);

    // Tag clicks in table (event delegation)
    tableBody.addEventListener("click", function (e) {
      var target = e.target;

      // Handle tag link clicks
      if (target.classList.contains("tag") && target.dataset.tag) {
        e.preventDefault();
        handleTagClick(target.dataset.tag);
        return;
      }

      // Handle action buttons
      if (target.tagName !== "BUTTON") return;
      var index = parseInt(target.dataset.index, 10);

      if (target.classList.contains("btn-row-edit")) {
        editRow(index);
      } else if (target.classList.contains("btn-delete")) {
        deleteRow(index);
      } else if (target.classList.contains("btn-row-save")) {
        saveRow(index);
      } else if (target.classList.contains("btn-row-cancel")) {
        render();
      }
    });

    // Filter chip remove (event delegation)
    activeFilters.addEventListener("click", function (e) {
      var btn = e.target.closest(".chip-remove");
      if (!btn) return;
      removeFilter(btn.dataset.type, btn.dataset.value);
    });

    // Browser back/forward
    window.addEventListener("popstate", function () {
      readURL();
      applySearch();
    });
  }

  // === Start ===
  init();
})();
