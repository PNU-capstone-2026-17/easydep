from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _source(path: str) -> str:
    return (FRONTEND / path).read_text(encoding="utf-8")


def test_svelte_workbench_has_one_conversation_and_artifact_workspace() -> None:
    source = _source("src/routes/workspace/+page.svelte")

    assert "<ChatTimeline" in source
    assert "<ArtifactPane" in source
    assert "<ArtifactNavigator" in _source("src/lib/components/ArtifactPane.svelte")
    assert "<Composer" in source
    assert "<StageRail" in source
    assert "connectEvents" in source
    assert "h-dvh min-h-0 overflow-hidden" in source
    assert "overflow-y-auto overscroll-contain" in source
    assert "event.kind !== 'progress'" in source
    assert "Requirements analysis" in _source("src/lib/components/ChatTimeline.svelte")
    timeline = _source("src/lib/components/ChatTimeline.svelte")
    assert "progressSteps" in timeline
    assert "progressEvent === 'analysisStepFinished'" in timeline
    assert "event.kind !== 'status'" in timeline
    assert "activeSpecTasks" in timeline
    assert "step.id === 'generate_specs'" in timeline


def test_artifact_pane_keeps_internal_requirement_contracts_out_of_primary_navigation() -> None:
    source = _source("src/lib/components/ArtifactPane.svelte")
    visualization = _source("src/lib/components/ArtifactVisualization.svelte")

    assert "<ArtifactVisualization" in source
    assert "Raw source" in source
    assert "Deployment capabilities" not in source
    assert "Resource constraints" not in source
    assert "stage === 'capability_contract'" not in visualization
    assert "stage === 'resource_intake'" not in visualization
    assert "internalArtifactTypes" in _source("src/routes/workspace/+page.svelte")


def test_refined_requirements_are_grouped_by_functional_classification() -> None:
    visualization = _source("src/lib/components/ArtifactVisualization.svelte")

    assert "Functional requirements" in visualization
    assert "Non-functional requirements" in visualization
    assert "requirementGroups" in visualization


def test_resource_questions_are_rendered_and_answered_in_the_conversation() -> None:
    timeline = _source("src/lib/components/ChatTimeline.svelte")
    composer = _source("src/lib/components/Composer.svelte")

    assert "event.metadata?.resource_question?.why" in timeline
    assert "resourceQuestion.choices" not in composer
    assert "Reply to the question below." in composer
    assert "Continue without this optional input" in composer


def test_initial_deployment_card_collects_coordinates_and_budget_only() -> None:
    source = _source("src/lib/components/DeploymentPreferencesCard.svelte")

    assert "Monthly budget" in source
    assert "selectedProviders" in source
    assert "selectedRegions" in source
    assert "Compute topology" not in source
    assert "Public HTTPS ingress" not in source
    assert "PostgreSQL placement" not in source
    assert "Placement zones" not in source
    assert "bind:value={replicaCount}" not in source
    assert "compute_profile:" not in source
    assert "replica_count:" not in source


def test_failed_design_step_can_be_retried_from_the_composer() -> None:
    composer = _source("src/lib/components/Composer.svelte")

    assert "command?.status === 'FAILED' && command.stage === 'design'" in composer
    assert "onAction('retry_design'" in composer
    assert "Completed design artifacts will be kept." in composer


def test_workspace_focuses_each_new_or_revised_artifact_result() -> None:
    workspace = _source("src/routes/workspace/+page.svelte")
    pane = _source("src/lib/components/ArtifactPane.svelte")

    assert "artifactSnapshotSignatures" in workspace
    assert "nextSignatures[stage] !== artifactSignatures[stage]" in workspace
    assert "selectedArtifact = latestArtifact" in workspace
    assert "function reviewArtifact" in workspace
    assert "artifactOpen = true" in workspace
    assert "if (window.innerWidth >= 900) artifactOpen = true" in workspace
    assert "tab = 'artifact'" in pane
    assert "requirementsArtifactTypes.has(selected)" in pane


def test_artifacts_are_conversational_and_open_in_the_detail_sidebar() -> None:
    workspace = _source("src/routes/workspace/+page.svelte")
    timeline = _source("src/lib/components/ChatTimeline.svelte")
    card = _source("src/lib/components/ArtifactConversationCard.svelte")
    pane = _source("src/lib/components/ArtifactPane.svelte")
    navigator = _source("src/lib/components/ArtifactNavigator.svelte")

    assert "grid-cols-[minmax(440px,1fr)_minmax(400px,42%)]" in workspace
    assert "onArtifactSelect={reviewArtifact}" in workspace
    assert "onClose={() => (artifactOpen = false)}" in workspace
    assert "<ArtifactConversationCard" in timeline
    assert "eventArtifactStages" in timeline
    assert "Generated artifacts" in timeline
    assert "Open ${artifactLabels[stage]" in card
    assert "<ArtifactNavigator" in pane
    assert "Artifact index" in pane
    assert "h-12 shrink-0" in pane
    assert "indexOpen" in pane
    assert "absolute left-3 right-3 top-12" in pane
    assert "grid-cols-3" in navigator


def test_diagrams_can_be_opened_in_an_expanded_view() -> None:
    pane = _source("src/lib/components/ArtifactPane.svelte")
    viewport = _source("src/lib/components/DraggableDiagramViewport.svelte")

    assert "diagramExpanded" in pane
    assert "Click to expand" in pane
    assert 'role="dialog"' in pane
    assert 'aria-modal="true"' in pane
    assert "event.key === 'Escape'" in pane
    assert "Close expanded diagram" in pane
    assert "<DraggableDiagramViewport" in pane
    assert "setPointerCapture" in viewport
    assert "scrollLeft = startScrollLeft" in viewport
    assert "touch-action: none" in viewport
    assert "[scrollbar-width:none]" in viewport


def test_workspace_preserves_the_four_development_stage_labels() -> None:
    source = _source("src/lib/components/StageRail.svelte")

    for stage in ("Requirements", "Design", "Implementation", "Testing"):
        assert stage in source


def test_auto_mode_advances_only_when_user_input_is_not_required() -> None:
    workspace = _source("src/routes/workspace/+page.svelte")
    composer = _source("src/lib/components/Composer.svelte")
    policy = _source("src/lib/auto-mode.ts")

    assert "easydep:auto-mode" in workspace
    assert "nextAutoAction(current)" in workspace
    assert "onToggleAutoMode={toggleAutoMode}" in workspace
    assert "aria-pressed={autoMode}" in composer
    assert "<Zap size={14}" in composer
    auto_button = composer.index("onclick={onToggleAutoMode}")
    send_button = composer.index('aria-label="Send message"')
    assert auto_button < send_button
    assert "result.action === 'confirm_change'" in policy
    assert "resourceQuestion.kind === 'suggested'" in policy
    assert "result.kind === 'question'" in policy
    assert "approve_implementation" in policy
    assert "start_testing" in policy


def test_sidebar_toggle_is_outside_the_clipped_content_boundary() -> None:
    source = _source("src/lib/components/AppSidebar.svelte")

    assert "shrink-0 overflow-visible transition-[width]" in source
    assert "w-full flex-col overflow-hidden border-r" in source
    assert "absolute -right-3 top-20 z-30" in source


def test_runtime_frontend_uses_english_ui_text() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((FRONTEND / "src").rglob("*"))
        if path.is_file() and path.suffix in {".js", ".svelte", ".ts"}
    )

    assert not any("가" <= character <= "힣" for character in source)


def test_generated_application_frontend_is_not_replaced_by_workbench_stack() -> None:
    package = _source("package.json")
    root_package = (ROOT / "app" / "implementation" / "templates" / "frontend" / "package.json")

    assert '"svelte"' in package
    if root_package.exists():
        assert '"svelte"' not in root_package.read_text(encoding="utf-8")


def test_static_server_uses_sveltekit_build_output() -> None:
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'FRONTEND_BUILD_DIR = BASE_DIR / "frontend" / "build"' in server
    assert "frontend-build" in dockerfile
    assert "COPY --from=frontend-build /src/build ./frontend/build" in dockerfile


def test_workspace_command_and_event_routes_are_registered() -> None:
    from server import app

    paths = {route.path for route in app.routes}
    assert "/api/workspace/apps" in paths
    assert "/api/workspace/apps/{app_id}" in paths
    assert "/api/workspace/apps/{app_id}/commands" in paths
    assert "/api/workspace/apps/{app_id}/events" in paths
    assert "/api/apps/{app_id}/design/retry" in paths
