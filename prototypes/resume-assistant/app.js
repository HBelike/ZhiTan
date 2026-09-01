const currentUser = {
  name: "郭琳琳",
  role: "member",
};

const jobTitles = [
  "Java Agent 开发工程师",
  "AI 应用开发工程师",
  "大模型平台后端工程师",
  "RAG 应用开发工程师",
  "Python Agent 工程师",
  "智能体平台研发工程师",
  "LLM 应用研发工程师",
  "多模态应用开发工程师",
];

const creators = ["郭琳琳", "郭琳琳", "郭琳琳", "林远", "郭琳琳", "陈墨"];

let tasks = Array.from({ length: 24 }, (_, index) => {
  const day = 17 - Math.floor(index / 4);
  const hour = 9 + (index % 4) * 2;
  return {
    id: index + 1,
    jobTitle: jobTitles[index % jobTitles.length],
    creator: creators[index % creators.length],
    createdDate: `2026-08-${String(day).padStart(2, "0")}`,
    createdAt: `2026-08-${String(day).padStart(2, "0")} ${String(hour).padStart(2, "0")}:30`,
  };
});

const taskGrid = document.querySelector("#taskGrid");
const homeView = document.querySelector("#homeView");
const workspaceView = document.querySelector("#workspaceView");
const detailView = document.querySelector("#detailView");
const detailTitle = document.querySelector("#detailTitle");
const detailCreator = document.querySelector("#detailCreator");
const detailCreatedAt = document.querySelector("#detailCreatedAt");
const previewOverlay = document.querySelector("#previewOverlay");
const toast = document.querySelector("#toast");
const recordCount = document.querySelector("#recordCount");
const jobTitleFilter = document.querySelector("#jobTitleFilter");
const dateFromFilter = document.querySelector("#dateFromFilter");
const dateToFilter = document.querySelector("#dateToFilter");

const workspaceJobTitle = document.querySelector("#workspaceJobTitle");
const resumeFileInput = document.querySelector("#resumeFileInput");
const resumeFileName = document.querySelector("#resumeFileName");
const canvasSourceFileName = document.querySelector("#canvasSourceFileName");
const sourceResumeEmpty = document.querySelector("#sourceResumeEmpty");
const sourceResumeContent = document.querySelector("#sourceResumeContent");
const jobDescriptionInput = document.querySelector("#jobDescriptionInput");
const extraPromptInput = document.querySelector("#extraPromptInput");
const jobImageInput = document.querySelector("#jobImageInput");
const jobImageName = document.querySelector("#jobImageName");
const analysisStage = document.querySelector("#analysisStage");
const analysisEmpty = document.querySelector("#analysisEmpty");
const analysisLoading = document.querySelector("#analysisLoading");
const suggestionContent = document.querySelector("#suggestionContent");
const optimizedResumeContent = document.querySelector("#optimizedResumeContent");
const suggestionTab = document.querySelector("#suggestionTab");
const optimizedResumeTab = document.querySelector("#optimizedResumeTab");
const regenerateButton = document.querySelector("#regenerateButton");
const generateResumeButton = document.querySelector("#generateResumeButton");
const saveRecordButton = document.querySelector("#saveRecordButton");

let analysisTimer = null;
let workspacePhase = "setup";
let activeTask = null;

function getPermittedTasks() {
  if (currentUser.role === "admin") return tasks;
  return tasks.filter((task) => task.creator === currentUser.name);
}

function filterTasks() {
  const keyword = jobTitleFilter.value.trim().toLocaleLowerCase("zh-CN");
  const dateFrom = dateFromFilter.value;
  const dateTo = dateToFilter.value;

  return getPermittedTasks().filter((task) => {
    const matchesTitle = !keyword || task.jobTitle.toLocaleLowerCase("zh-CN").includes(keyword);
    const matchesFrom = !dateFrom || task.createdDate >= dateFrom;
    const matchesTo = !dateTo || task.createdDate <= dateTo;
    return matchesTitle && matchesFrom && matchesTo;
  });
}

function renderTasks(list = filterTasks()) {
  recordCount.textContent = `${list.length} 条 · 第 1 页`;

  if (!list.length) {
    taskGrid.innerHTML = '<div class="empty-records">没有符合当前筛选条件的优化记录</div>';
    return;
  }

  taskGrid.innerHTML = list.map((task) => `
    <button class="task-card" data-task-id="${task.id}" aria-label="查看 ${task.jobTitle} 优化记录">
      <h3>${task.jobTitle}</h3>
      <dl class="task-meta">
        <div><dt>创建人</dt><dd>${task.creator}</dd></div>
        <div><dt>创建时间</dt><dd>${task.createdAt}</dd></div>
      </dl>
    </button>
  `).join("");
}

function switchView(targetView) {
  [homeView, workspaceView, detailView].forEach((view) => view.classList.remove("active-view"));
  targetView.classList.add("active-view");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showDetail(taskId) {
  const task = getPermittedTasks().find((item) => item.id === Number(taskId));
  if (!task) {
    showToast("当前账号无权查看这条记录");
    return;
  }

  activeTask = task;
  detailTitle.textContent = task.jobTitle;
  detailCreator.textContent = task.creator;
  detailCreatedAt.textContent = task.createdAt;
  switchView(detailView);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 1800);
}

function setAssistantView(viewName) {
  const showSuggestions = viewName === "suggestions";
  suggestionTab.classList.toggle("active", showSuggestions);
  optimizedResumeTab.classList.toggle("active", !showSuggestions);
  suggestionContent.classList.toggle("hidden", !showSuggestions);
  optimizedResumeContent.classList.toggle("hidden", showSuggestions);
}

function resetWorkspace() {
  window.clearTimeout(analysisTimer);
  workspacePhase = "setup";
  workspaceJobTitle.value = "";
  resumeFileInput.value = "";
  jobImageInput.value = "";
  jobDescriptionInput.value = "";
  extraPromptInput.value = "";
  resumeFileName.textContent = "选择 DOC、DOCX 或 PDF";
  canvasSourceFileName.textContent = "尚未选择文件";
  jobImageName.textContent = "未上传截图";
  sourceResumeEmpty.classList.remove("hidden");
  sourceResumeContent.classList.add("hidden");
  analysisEmpty.classList.remove("hidden");
  analysisLoading.classList.add("hidden");
  suggestionContent.classList.add("hidden");
  optimizedResumeContent.classList.add("hidden");
  suggestionTab.classList.add("active");
  optimizedResumeTab.classList.remove("active");
  optimizedResumeTab.disabled = true;
  regenerateButton.classList.add("hidden");
  generateResumeButton.classList.add("hidden");
  saveRecordButton.classList.add("hidden");
  analysisStage.textContent = "等待材料";
}

function openWorkspace() {
  activeTask = null;
  resetWorkspace();
  switchView(workspaceView);
}

function runAnalysis() {
  if (!resumeFileInput.files.length) {
    showToast("请先选择原始简历");
    return;
  }
  if (!jobDescriptionInput.value.trim() && !jobImageInput.files.length) {
    showToast("请填写岗位介绍或上传岗位截图");
    return;
  }

  workspacePhase = "analyzing";
  analysisStage.textContent = "分析中";
  analysisEmpty.classList.add("hidden");
  suggestionContent.classList.add("hidden");
  optimizedResumeContent.classList.add("hidden");
  analysisLoading.classList.remove("hidden");
  suggestionTab.classList.add("active");
  optimizedResumeTab.classList.remove("active");
  optimizedResumeTab.disabled = true;
  regenerateButton.classList.add("hidden");
  generateResumeButton.classList.add("hidden");
  saveRecordButton.classList.add("hidden");

  window.clearTimeout(analysisTimer);
  analysisTimer = window.setTimeout(() => {
    workspacePhase = "review";
    analysisLoading.classList.add("hidden");
    suggestionContent.classList.remove("hidden");
    analysisStage.textContent = "意见待确认";
    regenerateButton.classList.remove("hidden");
    generateResumeButton.classList.remove("hidden");
  }, 720);
}

function generateOptimizedResume() {
  const acceptedCount = suggestionContent.querySelectorAll('input[type="checkbox"]:checked').length;
  if (!acceptedCount) {
    showToast("请至少选择一条优化意见");
    return;
  }

  workspacePhase = "generated";
  optimizedResumeTab.disabled = false;
  setAssistantView("optimized");
  analysisStage.textContent = "简历已生成";
  generateResumeButton.classList.add("hidden");
  saveRecordButton.classList.remove("hidden");
  showToast(`已采纳 ${acceptedCount} 条意见并生成优化版`);
}

function saveWorkspaceRecord() {
  if (workspacePhase !== "generated") return;

  const now = new Date();
  const createdDate = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
  const createdAt = `${createdDate} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const record = {
    id: Math.max(...tasks.map((task) => task.id), 0) + 1,
    jobTitle: workspaceJobTitle.value.trim() || "未命名岗位",
    creator: currentUser.name,
    createdDate,
    createdAt,
  };

  tasks = [record, ...tasks];
  jobTitleFilter.value = "";
  dateFromFilter.value = "";
  dateToFilter.value = "";
  renderTasks();
  switchView(homeView);
  showToast("已保存到优化记录");
}

function openPreview(type) {
  const optimized = type === "optimized";
  const selectedFileName = resumeFileInput.files[0]?.name || "郭琳琳-后端开发.docx";
  document.querySelector("#previewRole").textContent = optimized ? "优化后简历" : "原始简历";
  document.querySelector("#previewFileName").textContent = optimized
    ? `${workspaceJobTitle.value.trim() || activeTask?.jobTitle || "Agent开发"}-优化版.docx`
    : selectedFileName;
  previewOverlay.classList.add("open");
  previewOverlay.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

taskGrid.addEventListener("click", (event) => {
  const card = event.target.closest("[data-task-id]");
  if (card) showDetail(card.dataset.taskId);
});

document.querySelector("#searchButton").addEventListener("click", () => renderTasks());

document.querySelector("#resetSearchButton").addEventListener("click", () => {
  jobTitleFilter.value = "";
  dateFromFilter.value = "";
  dateToFilter.value = "";
  renderTasks();
});

jobTitleFilter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") renderTasks();
});

resumeFileInput.addEventListener("change", () => {
  const file = resumeFileInput.files[0];
  resumeFileName.textContent = file?.name || "选择 DOC、DOCX 或 PDF";
  canvasSourceFileName.textContent = file?.name || "尚未选择文件";
  sourceResumeEmpty.classList.toggle("hidden", Boolean(file));
  sourceResumeContent.classList.toggle("hidden", !file);
});

jobImageInput.addEventListener("change", () => {
  jobImageName.textContent = jobImageInput.files[0]?.name || "未上传截图";
});

suggestionTab.addEventListener("click", () => {
  if (workspacePhase === "review" || workspacePhase === "generated") setAssistantView("suggestions");
});

optimizedResumeTab.addEventListener("click", () => {
  if (!optimizedResumeTab.disabled) setAssistantView("optimized");
});

document.querySelector("#createTaskButton").addEventListener("click", openWorkspace);
document.querySelector("#cancelWorkspaceButton").addEventListener("click", () => {
  window.clearTimeout(analysisTimer);
  switchView(homeView);
});
document.querySelector("#startAnalysisButton").addEventListener("click", runAnalysis);
regenerateButton.addEventListener("click", runAnalysis);
generateResumeButton.addEventListener("click", generateOptimizedResume);
saveRecordButton.addEventListener("click", saveWorkspaceRecord);

document.querySelector("#backButton").addEventListener("click", () => switchView(homeView));

document.addEventListener("click", (event) => {
  const preview = event.target.closest("[data-preview]");
  if (preview) openPreview(preview.dataset.preview);

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "download-docx") showToast("原型：开始下载 DOCX");
  if (action === "download-pdf") showToast("原型：开始下载 PDF");
  if (action === "review") showToast("原型：查看本次已采纳的修改建议");
});

document.querySelector("#closePreview").addEventListener("click", () => {
  previewOverlay.classList.remove("open");
  previewOverlay.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
});

document.querySelector("#overlayDownload").addEventListener("click", () => {
  showToast("原型：开始下载当前预览文件");
});

renderTasks();
