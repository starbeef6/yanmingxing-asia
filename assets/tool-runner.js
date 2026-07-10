(function () {
  var API_BASE_URL = "https://file-hub-api-smuzijgeaq-as.a.run.app";
  var tools = {
    "supply-summary": {
      title: "供应链汇总处理",
      endpoint: "/api/tools/supply-summary/run",
      accept: ".xlsx,.xls,.csv",
      multiple: true,
      description: "上传供应链原始表，后端 Python 脚本处理后返回结果文件。"
    },
    "inventory-check": {
      title: "库存与缺货检查",
      endpoint: "/api/tools/inventory-check/run",
      accept: ".xlsx,.xls,.csv",
      multiple: true,
      description: "上传库存或销售相关表，后端输出缺货、异常或周转检查结果。"
    },
    "vendor-reconcile": {
      title: "供应商对账处理",
      endpoint: "/api/tools/vendor-reconcile/run",
      accept: ".xlsx,.xls,.csv",
      multiple: true,
      description: "上传供应商对账文件，后端输出匹配和差异结果。"
    }
  };

  var page = document.querySelector("[data-tool-id]");
  if (!page) {
    return;
  }

  var toolId = page.getAttribute("data-tool-id");
  var tool = tools[toolId];
  var fileInput = document.querySelector("[data-file-input]");
  var dropZone = document.querySelector("[data-drop-zone]");
  var fileList = document.querySelector("[data-file-list]");
  var runButton = document.querySelector("[data-run-tool]");
  var clearButton = document.querySelector("[data-clear-files]");
  var statusBox = document.querySelector("[data-status]");
  var downloadLink = document.querySelector("[data-download-link]");
  var tokenInput = document.querySelector("[data-api-token]");
  var titleNode = document.querySelector("[data-tool-title]");
  var descriptionNode = document.querySelector("[data-tool-description]");
  var endpointNode = document.querySelector("[data-tool-endpoint]");
  var selectedFiles = [];
  var downloadUrl = "";

  function getToken() {
    return tokenInput ? tokenInput.value.trim() : "";
  }

  function setStatus(message, type) {
    statusBox.textContent = message;
    statusBox.classList.remove("error", "success");
    if (type) {
      statusBox.classList.add(type);
    }
  }

  function renderFiles() {
    fileList.innerHTML = "";
    if (!selectedFiles.length) {
      runButton.disabled = true;
      return;
    }

    selectedFiles.forEach(function (file) {
      var row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = "<strong>" + file.name + "</strong><span>" + Math.ceil(file.size / 1024) + " KB</span>";
      fileList.appendChild(row);
    });
    runButton.disabled = false;
  }

  function setFiles(fileLikeList) {
    selectedFiles = Array.prototype.slice.call(fileLikeList || []);
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      downloadUrl = "";
    }
    downloadLink.hidden = true;
    renderFiles();
    setStatus(selectedFiles.length ? "文件已选择。点击“开始处理”后会提交到后端接口。" : "请选择要处理的文件。");
  }

  function getDownloadName(response) {
    var disposition = response.headers.get("content-disposition") || "";
    var match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i);
    if (match) {
      return decodeURIComponent(match[1] || match[2]);
    }
    return toolId + "-result.xlsx";
  }

  async function runTool() {
    var formData = new FormData();
    selectedFiles.forEach(function (file) {
      formData.append("files", file, file.name);
    });

    if (!API_BASE_URL) {
      setStatus("后端地址尚未配置。", "error");
      return;
    }

    if (!getToken()) {
      setStatus("请输入访问令牌后再处理。", "error");
      return;
    }

    runButton.disabled = true;
    setStatus("正在上传并等待 Python 后端处理...");

    try {
      var response = await fetch(API_BASE_URL + tool.endpoint, {
        method: "POST",
        body: formData,
        headers: { "X-Token": getToken() }
      });

      if (!response.ok) {
        throw new Error("后端返回 " + response.status);
      }

      var blob = await response.blob();
      if (downloadUrl) {
        URL.revokeObjectURL(downloadUrl);
      }
      downloadUrl = URL.createObjectURL(blob);
      downloadLink.href = downloadUrl;
      downloadLink.download = getDownloadName(response);
      downloadLink.hidden = false;
      setStatus("处理完成，可以下载结果文件。", "success");
    } catch (error) {
      setStatus("处理失败：" + error.message, "error");
    } finally {
      runButton.disabled = !selectedFiles.length;
    }
  }

  if (!tool) {
    setStatus("工具配置不存在，请检查 data-tool-id。", "error");
    return;
  }

  document.title = tool.title + " - 严明星数据处理中心";
  titleNode.textContent = tool.title;
  descriptionNode.textContent = tool.description;
  endpointNode.textContent = tool.endpoint;
  if (tokenInput) {
    tokenInput.value = window.localStorage.getItem("file-hub-api-token") || "";
    tokenInput.addEventListener("change", function () {
      window.localStorage.setItem("file-hub-api-token", getToken());
    });
  }
  fileInput.accept = tool.accept;
  fileInput.multiple = tool.multiple;
  renderFiles();

  dropZone.addEventListener("click", function () {
    fileInput.click();
  });

  dropZone.addEventListener("dragover", function (event) {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });

  dropZone.addEventListener("dragleave", function () {
    dropZone.classList.remove("dragging");
  });

  dropZone.addEventListener("drop", function (event) {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    setFiles(event.dataTransfer.files);
  });

  fileInput.addEventListener("change", function (event) {
    setFiles(event.target.files);
  });

  clearButton.addEventListener("click", function () {
    fileInput.value = "";
    setFiles([]);
  });

  runButton.addEventListener("click", runTool);
})();
