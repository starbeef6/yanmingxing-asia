(function () {
  var config = window.PORTAL_CONFIG;
  var page = document.querySelector('[data-page="runner"]');
  if (!config || !page) {
    return;
  }

  var query = new URLSearchParams(window.location.search);
  var departmentId = query.get("department") || "";
  var toolId = query.get("id") || "";
  var department = config.departments.find(function (item) {
    return item.id === departmentId;
  });
  var tool = department && department.tools.find(function (item) {
    return item.id === toolId;
  });

  var fileInput = document.querySelector("[data-file-input]");
  var dropZone = document.querySelector("[data-drop-zone]");
  var fileList = document.querySelector("[data-file-list]");
  var runButton = document.querySelector("[data-run-tool]");
  var clearButton = document.querySelector("[data-clear-files]");
  var downloadLink = document.querySelector("[data-download-link]");
  var tokenInput = document.querySelector("[data-api-token]");
  var tokenSettings = document.querySelector("[data-token-settings]");
  var uploadStatus = document.querySelector("[data-upload-status]");
  var processStatus = document.querySelector("[data-process-status]");
  var uploadStep = document.querySelector('[data-workflow-step="upload"]');
  var processStep = document.querySelector('[data-workflow-step="process"]');
  var downloadStep = document.querySelector('[data-workflow-step="download"]');
  var selectedFiles = [];
  var downloadUrl = "";

  function setFeedback(node, state, message, symbol) {
    node.className = "feedback-item" + (state ? " " + state : "");
    node.querySelector(".feedback-dot").textContent = symbol || "";
    node.querySelector("span:last-child").textContent = message;
  }

  function setStep(activeStep) {
    [uploadStep, processStep, downloadStep].forEach(function (step) {
      step.classList.remove("active", "done");
    });
    if (activeStep === "upload") {
      uploadStep.classList.add("active");
    }
    if (activeStep === "process") {
      uploadStep.classList.add("done");
      processStep.classList.add("active");
    }
    if (activeStep === "download") {
      uploadStep.classList.add("done");
      processStep.classList.add("done");
      downloadStep.classList.add("active");
    }
  }

  function getToken() {
    return tokenInput.value.trim();
  }

  function resetDownload() {
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      downloadUrl = "";
    }
    downloadLink.hidden = true;
  }

  function formatSize(bytes) {
    if (bytes < 1024 * 1024) {
      return Math.max(1, Math.ceil(bytes / 1024)) + " KB";
    }
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function renderFiles() {
    fileList.innerHTML = "";
    selectedFiles.forEach(function (file) {
      var row = document.createElement("div");
      var name = document.createElement("strong");
      var size = document.createElement("span");
      row.className = "file-row";
      name.textContent = file.name;
      size.textContent = formatSize(file.size);
      row.appendChild(name);
      row.appendChild(size);
      fileList.appendChild(row);
    });
    runButton.disabled = !selectedFiles.length;
    clearButton.hidden = !selectedFiles.length;
  }

  function setFiles(files) {
    selectedFiles = Array.prototype.slice.call(files || []);
    resetDownload();
    renderFiles();
    setStep("upload");
    if (selectedFiles.length) {
      setFeedback(uploadStatus, "success", "文件已添加，等待上传", "✓");
    } else {
      setFeedback(uploadStatus, "", "等待上传", "1");
    }
    setFeedback(processStatus, "", "等待处理", "2");
  }

  function getDownloadName(response) {
    var disposition = response.headers.get("content-disposition") || "";
    var match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^\"]+)"?/i);
    if (match) {
      return decodeURIComponent(match[1] || match[2]);
    }
    return tool.id + "-result.zip";
  }

  async function runTool() {
    if (!selectedFiles.length) {
      return;
    }
    if (!getToken()) {
      tokenSettings.open = true;
      tokenInput.focus();
      setFeedback(processStatus, "error", "请先填写访问令牌", "!");
      return;
    }

    window.localStorage.setItem("file-hub-api-token", getToken());
    resetDownload();
    runButton.disabled = true;
    setStep("process");
    setFeedback(uploadStatus, "working", "正在上传文件", "");
    setFeedback(processStatus, "working", "正在运行处理流程", "");

    var formData = new FormData();
    selectedFiles.forEach(function (file) {
      formData.append("files", file, file.name);
    });

    try {
      var response = await fetch(config.apiBaseUrl + tool.endpoint, {
        method: "POST",
        body: formData,
        headers: { "X-Token": getToken() }
      });
      if (!response.ok) {
        throw new Error(response.status === 401 ? "访问令牌不正确" : "服务返回 " + response.status);
      }

      setFeedback(uploadStatus, "success", "上传成功", "✓");
      var blob = await response.blob();
      downloadUrl = URL.createObjectURL(blob);
      downloadLink.href = downloadUrl;
      downloadLink.download = getDownloadName(response);
      downloadLink.hidden = false;
      setFeedback(processStatus, "success", "处理成功", "✓");
      setStep("download");
    } catch (error) {
      setFeedback(uploadStatus, "error", "上传或连接失败", "!");
      setFeedback(processStatus, "error", error.message, "!");
      setStep("upload");
    } finally {
      runButton.disabled = !selectedFiles.length;
    }
  }

  if (!department || !tool) {
    document.querySelector("[data-tool-title]").textContent = "处理流程不存在";
    document.querySelector("[data-tool-description]").textContent = "请返回部门页面重新选择。";
    dropZone.hidden = true;
    runButton.hidden = true;
    return;
  }

  document.title = tool.name + " - 数据处理中心";
  document.querySelector("[data-tool-title]").textContent = tool.name;
  document.querySelector("[data-tool-description]").textContent = "上传文件，处理完成后下载结果。";
  document.querySelector("[data-department-label]").textContent = department.name;
  document.querySelector("[data-back-link]").href = "../department/?id=" + encodeURIComponent(department.id);
  document.querySelector("[data-file-hint]").textContent = tool.multiple ? "支持 Excel 和 CSV，可选择多个文件" : "支持 Excel 和 CSV";
  fileInput.accept = tool.accept;
  fileInput.multiple = tool.multiple;
  tokenInput.value = window.localStorage.getItem("file-hub-api-token") || "";

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
  tokenInput.addEventListener("change", function () {
    window.localStorage.setItem("file-hub-api-token", getToken());
  });
  runButton.addEventListener("click", runTool);
  setFiles([]);
})();
