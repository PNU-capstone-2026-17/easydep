(() => {
  const APP_ID_KEY = "easydep_app_id";
  const IMPLEMENTATION_JOB_PREFIX = "easydep_implementation_job_";
  const ARTIFACT_TYPES = [
    ["SOURCE_CODE", "백엔드 소스"],
    ["FRONTEND_SOURCE_CODE", "프론트엔드 소스"],
    ["TEST_CODE", "테스트 코드"],
    ["DEPLOYMENT_FILE", "Docker·배포 파일"],
    ["IAC_CODE", "Terraform"],
  ];

  const el = (id) => document.getElementById(id);
  const state = { appId: "", implementationJobId: "", poller: null };

  const statusClass = (status) => ({
    COMPLETED: "completed", RUNNING: "running", GENERATING: "planning", PLANNING: "planning", QUEUED: "queued",
    VALIDATING_INPUT: "planning", REUSING_GENERATED_RUN: "planning", PREPARING_FEEDBACK: "planning",
    GENERATING_SOURCES: "planning", PREPARING_BUILD: "planning", VERIFYING: "running",
    AWAITING_APPROVAL: "input", NEEDS_INPUT: "input", NEEDS_PLANNER: "warning",
    FAILED: "failed", CANCELLED: "failed", REJECTED: "rejected", PASSED: "passed",
  }[status] || "");

  const label = (status) => ({
    QUEUED: "대기 중", GENERATING: "초기 산출물 생성 중", VALIDATING_INPUT: "입력 검증 중", REUSING_GENERATED_RUN: "기존 생성 결과 재사용 중",
    PREPARING_FEEDBACK: "피드백 산출물 준비 중", GENERATING_SOURCES: "소스 코드 생성 중",
    PREPARING_BUILD: "빌드 준비 중", VERIFYING: "생성 코드 컴파일 검증 중",
    PLANNING: "구현 작업 계획 중", AWAITING_APPROVAL: "승인 대기",
    RUNNING: "구현 중", COMPLETED: "구현 완료", FAILED: "실패", CANCELLED: "취소됨",
    REJECTED: "거부됨", NEEDS_INPUT: "추가 입력 필요", NEEDS_PLANNER: "계획 보완 필요",
    PASSED: "통과", COMPLETED_TEST: "테스트 완료",
  }[status] || status || "대기");

  function setNotice(id, text, tone = "") {
    const target = el(id);
    target.textContent = text;
    target.className = `notice ${tone}`.trim();
  }

  function setStatus(id, status, customLabel = "") {
    const target = el(id);
    target.textContent = customLabel || label(status);
    target.className = `status-chip ${statusClass(status)}`.trim();
  }

  function setAppId(value) {
    state.appId = value.trim();
    el("appId").value = state.appId;
    if (state.appId) localStorage.setItem(APP_ID_KEY, state.appId);
    else localStorage.removeItem(APP_ID_KEY);
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

  async function loadApp() {
    setAppId(el("appId").value);
    if (!state.appId) {
      setNotice("appMessage", "App ID를 입력하세요.", "error");
      return false;
    }
    el("loadApp").disabled = true;
    try {
      const data = await request(`/api/apps/${encodeURIComponent(state.appId)}`);
      const artifacts = data.artifacts || {};
      const validation = data.validation || {};
      const findings = Object.entries(validation).flatMap(([stage, value]) =>
        (value?.findings || []).map((finding) => ({ stage, finding }))
      );
      const ready = Boolean(artifacts.class_diagram && artifacts.api_spec) && !findings.length;
      setNotice(
        "appMessage",
        ready
          ? "검증된 설계가 준비되었습니다. 구현 작업을 시작할 수 있습니다."
          : findings.length
            ? `설계 불일치 ${findings.length}건이 남아 있습니다. 설계 화면에서 수정·재검증한 뒤 구현을 시작하세요.`
            : "클래스 다이어그램과 OpenAPI 명세를 설계 화면에서 먼저 완료하세요.",
        ready ? "success" : ""
      );
      state.implementationJobId = localStorage.getItem(implementationStorageKey()) || "";
      if (state.implementationJobId) pollImplementation();
      return ready;
    } catch (error) {
      setNotice("appMessage", `App ID를 불러오지 못했습니다: ${error.message}`, "error");
      return false;
    } finally {
      el("loadApp").disabled = false;
    }
  }

  function renderImplementation(job) {
    el("implementationJob").classList.remove("hidden");
    setStatus("implementationStatus", job.status);
    el("implementationJobId").textContent = job.job_id || "—";
    el("implementationUpdatedAt").textContent = job.updated_at ? new Date(job.updated_at).toLocaleString("ko-KR") : "—";
    el("implementationDetails").textContent = JSON.stringify({
      status: job.status, progress: job.progress, error: job.error, workflow: job.workflow,
      design_validation: job.design_validation, artifact_versions: job.artifact_versions,
    }, null, 2);
    const canCancel = ["QUEUED", "GENERATING", "VALIDATING_INPUT", "REUSING_GENERATED_RUN", "PREPARING_FEEDBACK", "GENERATING_SOURCES", "PREPARING_BUILD", "VERIFYING", "PLANNING", "RUNNING", "AWAITING_APPROVAL"].includes(job.status);
    el("cancelImplementation").classList.toggle("hidden", !canCancel);
    el("startImplementation").disabled = canCancel;
    el("approvalPanel").classList.toggle("hidden", job.status !== "AWAITING_APPROVAL");
    if (job.status === "AWAITING_APPROVAL") renderApproval(job.transmission_request || {});
    if (job.status === "COMPLETED") {
      setNotice("implementationMessage", "구현이 완료되었습니다. 산출물을 확인한 뒤 테스팅 탭에서 테스트를 실행하세요.", "success");
      loadArtifacts();
    } else if (job.status === "FAILED" || job.status === "NEEDS_INPUT" || job.status === "NEEDS_PLANNER") {
      setNotice(
        "implementationMessage",
        job.status === "NEEDS_INPUT" && job.design_validation
          ? "설계 불일치 때문에 구현을 시작하지 않았습니다. 설계 화면에서 수정·재검증한 뒤 새 구현 작업을 시작하세요."
          : job.error || "구현을 완료하지 못했습니다. 상세 결과를 확인하세요.",
        "error"
      );
    } else {
      setNotice("implementationMessage", `${label(job.status)}입니다. 상태를 자동으로 갱신합니다.`);
    }
  }

  function renderApproval(request) {
    const tasks = request.tasks || [];
    el("approvalTasks").innerHTML = tasks.length
      ? tasks.map((task) => `<li><strong>${escapeHtml(task.taskId || task.task_id || "작업")}</strong>${task.summary ? ` — ${escapeHtml(task.summary)}` : ""}</li>`).join("")
      : "<li>전송할 작업 정보를 준비하는 중입니다.</li>";
    setNotice("approvalMessage", request.reason || "승인하면 외부 에이전트 실행이 시작됩니다.");
  }

  async function startImplementation() {
    const ready = await loadApp();
    if (!ready) return;
    const basePackage = el("basePackage").value.trim();
    el("startImplementation").disabled = true;
    try {
      const job = await request(`/api/implementation/apps/${encodeURIComponent(state.appId)}/jobs`, {
        method: "POST",
        body: JSON.stringify({ base_package: basePackage || "com.easydep.app", allow_assumptions: true }),
      });
      state.implementationJobId = job.job_id;
      localStorage.setItem(implementationStorageKey(), job.job_id);
      renderImplementation(job);
      pollImplementation();
    } catch (error) {
      setNotice("implementationMessage", `구현 작업을 시작하지 못했습니다: ${error.message}`, "error");
      el("startImplementation").disabled = false;
    }
  }

  async function pollImplementation() {
    window.clearTimeout(state.poller);
    if (!state.implementationJobId) return;
    try {
      const job = await request(`/api/implementation/jobs/${encodeURIComponent(state.implementationJobId)}`);
      renderImplementation(job);
      if (["QUEUED", "GENERATING", "VALIDATING_INPUT", "REUSING_GENERATED_RUN", "PREPARING_FEEDBACK", "GENERATING_SOURCES", "PREPARING_BUILD", "VERIFYING", "PLANNING", "RUNNING"].includes(job.status)) {
        state.poller = window.setTimeout(pollImplementation, 2000);
      }
    } catch (error) {
      setNotice("implementationMessage", `구현 상태를 조회하지 못했습니다: ${error.message}`, "error");
    }
  }

  async function decideApproval(approved) {
    if (!state.implementationJobId) return;
    const button = approved ? el("approveImplementation") : el("rejectImplementation");
    button.disabled = true;
    try {
      const current = await request(`/api/implementation/jobs/${encodeURIComponent(state.implementationJobId)}`);
      const requestId = current.transmission_request?.requestId;
      if (!requestId) throw new Error("현재 승인 요청 정보를 찾을 수 없습니다.");
      const job = await request(`/api/implementation/jobs/${encodeURIComponent(state.implementationJobId)}/approval`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, approved, approved_by: "EasyDep UI", retry_failed: false, delegate_repair_approvals: true }),
      });
      renderImplementation(job);
      if (approved) pollImplementation();
    } catch (error) {
      setNotice("approvalMessage", `승인 상태를 변경하지 못했습니다: ${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function cancelImplementation() {
    if (!state.implementationJobId || !window.confirm("진행 중인 구현 작업을 취소할까요?")) return;
    try {
      const job = await request(`/api/implementation/jobs/${encodeURIComponent(state.implementationJobId)}/cancel`, { method: "POST" });
      renderImplementation(job);
    } catch (error) {
      setNotice("implementationMessage", `작업을 취소하지 못했습니다: ${error.message}`, "error");
    }
  }

  async function loadArtifacts() {
    if (!state.appId) return;
    const target = el("artifactList");
    const results = await Promise.all(ARTIFACT_TYPES.map(async ([type, name]) => {
      try {
        const snapshot = await request(`/api/implementation/apps/${encodeURIComponent(state.appId)}/artifacts/${type}`);
        return { type, name, snapshot };
      } catch (_) { return null; }
    }));
    const present = results.filter(Boolean);
    target.innerHTML = present.length ? present.map(({ name, snapshot }) => `
      <div class="artifact-row"><strong>${escapeHtml(name)}</strong><span>v${snapshot.version_no} · ${snapshot.files.length}개 파일</span></div>
    `).join("") : "<p class=\"notice\">완료된 파일 산출물이 없습니다.</p>";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>\"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[char]);
  }

  el("loadApp").addEventListener("click", loadApp);
  el("startImplementation").addEventListener("click", startImplementation);
  el("cancelImplementation").addEventListener("click", cancelImplementation);
  el("approveImplementation").addEventListener("click", () => decideApproval(true));
  el("rejectImplementation").addEventListener("click", () => decideApproval(false));
  el("appId").addEventListener("change", () => setAppId(el("appId").value));

  setAppId(localStorage.getItem(APP_ID_KEY) || "");
  if (state.appId) loadApp();
})();
