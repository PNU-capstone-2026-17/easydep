(() => {
  const APP_ID_KEY = "easydep_app_id";
  const appId = document.getElementById("appId");
  const message = document.getElementById("appMessage");
  const loadButton = document.getElementById("loadApp");
  const chips = {
    requirements: document.getElementById("requirementsStatus"),
    design: document.getElementById("designStatus"),
    implementation: document.getElementById("implementationStatus"),
    testing: document.getElementById("testingStatus"),
  };

  const setMessage = (text, tone = "") => {
    message.textContent = text;
    message.className = `notice ${tone}`.trim();
  };

  const setChip = (chip, label, state = "") => {
    chip.textContent = label;
    chip.className = `status-chip ${state}`.trim();
  };

  const saveAppId = (value) => {
    const normalized = value.trim();
    if (normalized) localStorage.setItem(APP_ID_KEY, normalized);
    else localStorage.removeItem(APP_ID_KEY);
  };

  const has = (artifacts, key) => Boolean(artifacts?.[key]);

  function renderState(data) {
    const artifacts = data.artifacts || {};
    const analyzed = has(artifacts, "refined_requirements") && has(artifacts, "usecase_spec");
    const designed = has(artifacts, "class_diagram") && has(artifacts, "api_spec");
    setChip(chips.requirements, analyzed ? "완료" : "진행 전", analyzed ? "completed" : "");
    setChip(chips.design, designed ? "완료" : analyzed ? "진행 가능" : "선행 단계 필요", designed ? "completed" : analyzed ? "ready" : "");
    setChip(chips.implementation, designed ? "설계 완료" : "설계 완료 후 가능", designed ? "ready" : "");
    setChip(chips.testing, "구현 완료 후 가능");
  }

  async function loadCurrentApp() {
    const value = appId.value.trim();
    if (!value) {
      setMessage("App ID를 입력하거나 요구사항 분석에서 새 프로젝트를 시작하세요.", "error");
      return;
    }
    loadButton.disabled = true;
    setMessage("산출물 상태를 불러오는 중입니다.");
    try {
      const response = await fetch(`/api/apps/${encodeURIComponent(value)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      saveAppId(value);
      renderState(data);
      setMessage("이 App ID의 산출물 상태를 불러왔습니다.", "success");
    } catch (error) {
      setMessage(`App ID를 불러오지 못했습니다: ${error.message}`, "error");
    } finally {
      loadButton.disabled = false;
    }
  }

  appId.value = localStorage.getItem(APP_ID_KEY) || "";
  appId.addEventListener("change", () => saveAppId(appId.value));
  loadButton.addEventListener("click", loadCurrentApp);
  if (appId.value) loadCurrentApp();
})();
