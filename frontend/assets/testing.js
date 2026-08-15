(() => {
  const APP_ID_KEY = "easydep_app_id";
  const IMPLEMENTATION_JOB_PREFIX = "easydep_implementation_job_";
  const TESTING_JOB_PREFIX = "easydep_testing_job_";
  const el = (id) => document.getElementById(id);
  const state = {
    appId: "", implementationJobId: "", testingJobId: "",
    implementationPoller: null, testingPoller: null,
  };

  const statusClass = (status) => ({
    COMPLETED: "completed", RUNNING: "running", PLANNING: "planning", QUEUED: "queued",
    AWAITING_APPROVAL: "input", NEEDS_INPUT: "input", NEEDS_PLANNER: "warning",
    FAILED: "failed", CANCELLED: "failed", REJECTED: "rejected", PASSED: "passed",
  }[status] || "");

  const implementationLabel = (status) => ({
    QUEUED: "대기 중", PLANNING: "계획 생성 중", AWAITING_APPROVAL: "승인 대기",
    RUNNING: "구현 중", COMPLETED: "구현 완료", FAILED: "실패", CANCELLED: "취소됨",
    REJECTED: "거부됨", NEEDS_INPUT: "추가 입력 필요", NEEDS_PLANNER: "계획 보완 필요",
  }[status] || status || "대기");

  const testingLabel = (status) => ({
    QUEUED: "대기 중", RUNNING: "테스트 중", COMPLETED: "테스트 완료", FAILED: "실패",
  }[status] || status || "대기");

  function setNotice(id, text, tone = "") {
    const target = el(id);
    target.textContent = text;
    target.className = `notice ${tone}`.trim();
  }

  function setStatus(id, status, customLabel = "") {
    const target = el(id);
    target.textContent = customLabel || implementationLabel(status);
    target.className = `status-chip ${statusClass(status)}`.trim();
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function implementationStorageKey() { return IMPLEMENTATION_JOB_PREFIX + state.appId; }
  function testingStorageKey() { return TESTING_JOB_PREFIX + state.appId; }

  function setAppId(value) {
    state.appId = value.trim();
    el("appId").value = state.appId;
    if (state.appId) localStorage.setItem(APP_ID_KEY, state.appId);
    else localStorage.removeItem(APP_ID_KEY);
  }

  async function loadApp() {
    setAppId(el("appId").value);
    if (!state.appId) {
      setNotice("appMessage", "App ID를 입력하세요.", "error");
      return;
    }
    el("loadApp").disabled = true;
    try {
      const data = await request(`/api/apps/${encodeURIComponent(state.appId)}`);
      const artifacts = data.artifacts || {};
      const ready = Boolean(artifacts.class_diagram && artifacts.api_spec);
      setNotice(
        "appMessage",
        ready ? "설계 산출물을 확인했습니다. 완료된 구현 작업을 선택하세요." : "클래스 다이어그램과 OpenAPI 명세를 시스템 설계에서 먼저 완료하세요.",
        ready ? "success" : ""
      );
      state.implementationJobId = localStorage.getItem(implementationStorageKey()) || "";
      state.testingJobId = localStorage.getItem(testingStorageKey()) || "";
      el("implementationJobId").value = state.implementationJobId;
      if (state.implementationJobId) await loadImplementation();
      else renderImplementation(null);
      if (state.testingJobId) pollTesting();
    } catch (error) {
      setNotice("appMessage", `App ID를 불러오지 못했습니다: ${error.message}`, "error");
    } finally {
      el("loadApp").disabled = false;
    }
  }

  async function loadImplementation() {
    window.clearTimeout(state.implementationPoller);
    state.implementationJobId = el("implementationJobId").value.trim();
    if (!state.appId) {
      setNotice("implementationMessage", "App ID를 먼저 불러오세요.", "error");
      return;
    }
    if (!state.implementationJobId) {
      setNotice("implementationMessage", "구현 작업 ID를 입력하세요.", "error");
      renderImplementation(null);
      return;
    }
    el("loadImplementation").disabled = true;
    try {
      const job = await request(`/api/implementation/jobs/${encodeURIComponent(state.implementationJobId)}`);
      if (job.app_id !== state.appId) throw new Error("이 App ID에 속한 구현 작업이 아닙니다.");
      localStorage.setItem(implementationStorageKey(), job.job_id);
      renderImplementation(job);
      if (["QUEUED", "PLANNING", "RUNNING", "AWAITING_APPROVAL"].includes(job.status)) {
        state.implementationPoller = window.setTimeout(loadImplementation, 2000);
      }
    } catch (error) {
      setNotice("implementationMessage", `구현 상태를 조회하지 못했습니다: ${error.message}`, "error");
      renderImplementation(null);
    } finally {
      el("loadImplementation").disabled = false;
    }
  }

  function renderImplementation(job) {
    const completed = job?.status === "COMPLETED";
    el("startTesting").disabled = !completed;
    el("implementationJob").classList.toggle("hidden", !job);
    if (!job) return;
    setStatus("implementationStatus", job.status);
    el("implementationJobSummaryId").textContent = job.job_id || "—";
    el("implementationUpdatedAt").textContent = job.updated_at ? new Date(job.updated_at).toLocaleString("ko-KR") : "—";
    el("implementationDetails").textContent = JSON.stringify({
      status: job.status, error: job.error, workflow: job.workflow, artifact_versions: job.artifact_versions,
    }, null, 2);
    if (completed) {
      setNotice("implementationMessage", "구현이 완료되었습니다. 테스트를 실행할 수 있습니다.", "success");
    } else if (["FAILED", "NEEDS_INPUT", "NEEDS_PLANNER", "CANCELLED", "REJECTED"].includes(job.status)) {
      setNotice("implementationMessage", job.error || "구현이 완료되지 않았습니다. 구현 화면에서 상태를 확인하세요.", "error");
    } else {
      setNotice("implementationMessage", `${implementationLabel(job.status)}입니다. 완료되면 테스트 실행이 활성화됩니다.`);
    }
  }

  async function startTesting() {
    if (!state.appId || !state.implementationJobId) return;
    el("startTesting").disabled = true;
    try {
      const job = await request(`/api/testing/apps/${encodeURIComponent(state.appId)}/jobs`, {
        method: "POST", body: JSON.stringify({ implementation_job_id: state.implementationJobId }),
      });
      state.testingJobId = job.job_id;
      localStorage.setItem(testingStorageKey(), job.job_id);
      renderTesting(job);
      pollTesting();
    } catch (error) {
      setNotice("testingMessage", `테스트를 시작하지 못했습니다: ${error.message}`, "error");
      el("startTesting").disabled = false;
    }
  }

  function renderTesting(job) {
    el("testingJob").classList.remove("hidden");
    const result = job.result || {};
    const passed = job.status === "COMPLETED" && result.passed === true;
    setStatus("testingStatus", passed ? "PASSED" : job.status, passed ? "테스트 통과" : testingLabel(job.status));
    el("testingDetails").textContent = JSON.stringify({ status: job.status, result, error: job.error }, null, 2);
    if (job.status === "COMPLETED") {
      setNotice("testingMessage", passed ? "모든 애플리케이션 테스트가 통과했습니다." : "테스트 실행은 끝났지만 실패 또는 미통과 항목이 있습니다. 진단 결과를 확인하세요.", passed ? "success" : "error");
      el("startTesting").disabled = false;
    } else if (job.status === "FAILED") {
      setNotice("testingMessage", job.error || "테스트 실행 자체에 실패했습니다.", "error");
      el("startTesting").disabled = false;
    } else {
      setNotice("testingMessage", `${testingLabel(job.status)}입니다. 상태를 자동으로 갱신합니다.`);
    }
  }

  async function pollTesting() {
    window.clearTimeout(state.testingPoller);
    if (!state.testingJobId) return;
    try {
      const job = await request(`/api/testing/jobs/${encodeURIComponent(state.testingJobId)}`);
      renderTesting(job);
      if (["QUEUED", "RUNNING"].includes(job.status)) state.testingPoller = window.setTimeout(pollTesting, 2000);
    } catch (error) {
      setNotice("testingMessage", `테스트 상태를 조회하지 못했습니다: ${error.message}`, "error");
    }
  }

  el("loadApp").addEventListener("click", loadApp);
  el("loadImplementation").addEventListener("click", loadImplementation);
  el("startTesting").addEventListener("click", startTesting);
  el("appId").addEventListener("change", () => setAppId(el("appId").value));

  setAppId(localStorage.getItem(APP_ID_KEY) || "");
  if (state.appId) loadApp();
})();
