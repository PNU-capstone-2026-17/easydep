<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { PanelRightClose, PanelRightOpen, RefreshCw, Wifi, WifiOff } from '@lucide/svelte';
  import AppSidebar from '$lib/components/AppSidebar.svelte';
  import ArtifactPane from '$lib/components/ArtifactPane.svelte';
  import ChatTimeline from '$lib/components/ChatTimeline.svelte';
  import Composer from '$lib/components/Composer.svelte';
  import StageRail from '$lib/components/StageRail.svelte';
  import { connectEvents, getArtifacts, getCloudOptions, getFileArtifact, getWorkspace, listApps, saveDeploymentPreferences, sendCommand } from '$lib/api';
  import type { ArtifactDocument, CloudProvider, CloudRegionOption, DeploymentPreferences, FileArtifactSnapshot, Stage, WorkspaceApp, WorkspaceCommand, WorkspaceEvent } from '$lib/types';
  import { errorMessage } from '$lib/utils';
  import { Badge } from '$lib/components/ui/badge';
  import { Button } from '$lib/components/ui/button';
  import { artifactPresent, fileArtifactTypes, internalArtifactTypes } from '$lib/artifacts';
  import { nextAutoAction } from '$lib/auto-mode';

  const AUTO_MODE_STORAGE_KEY = 'easydep:auto-mode';

  let appId = $state('');
  let apps = $state<WorkspaceApp[]>([]);
  let events = $state<WorkspaceEvent[]>([]);
  let command = $state<WorkspaceCommand | null>(null);
  let currentStage = $state<Stage>('requirements');
  let artifacts = $state<ArtifactDocument | null>(null);
  let fileArtifacts = $state<Record<string, FileArtifactSnapshot>>({});
  let selectedArtifact = $state('refined_requirements');
  let sidebarCollapsed = $state(false);
  let artifactOpen = $state(true);
  let connected = $state(false);
  let loading = $state(true);
  let actionBusy = $state(false);
  let error = $state('');
  let source: EventSource | null = null;
  let artifactRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  let timelineScroller = $state<HTMLDivElement>();
  let followTimeline = $state(true);
  let initialized = false;
  let artifactSignatures = $state<Record<string, string>>({});
  let cloudRegions = $state<Record<CloudProvider, CloudRegionOption[]>>({ aws: [], azure: [], gcp: [] });
  let deploymentPreferences = $state<DeploymentPreferences | null>(null);
  let preferenceSaving = $state(false);
  let autoMode = $state(false);
  let autoActionKey = '';

  let busy = $derived(actionBusy || ['QUEUED', 'RUNNING'].includes(command?.status ?? ''));
  let selectedStage = $derived(
    fileArtifactTypes.includes(selectedArtifact)
      ? 'implementation'
      : ['refined_requirements', 'usecase_spec', 'usecase_diagram'].includes(selectedArtifact)
      ? 'requirements'
      : 'design'
  );

  onMount(() => {
    initialized = true;
    autoMode = window.localStorage.getItem(AUTO_MODE_STORAGE_KEY) === 'true';
    const syncLocation = () => {
      appId = new URL(window.location.href).searchParams.get('app') ?? '';
    };
    syncLocation();
    window.addEventListener('popstate', syncLocation);
    if (window.innerWidth < 900) {
      sidebarCollapsed = true;
      artifactOpen = false;
    }
    void refreshApps();
    getCloudOptions()
      .then((options) => {
        cloudRegions = options.regions;
      })
      .catch(() => undefined);
    return () => {
      source?.close();
      if (artifactRefreshTimer) clearTimeout(artifactRefreshTimer);
      window.removeEventListener('popstate', syncLocation);
    };
  });

  $effect(() => {
    if (!initialized) return;
    const id = appId;
    source?.close();
    if (id) void loadApp(id);
  });

  $effect(() => {
    events.length;
    void tick().then(() => {
      if (timelineScroller && followTimeline) {
        timelineScroller.scrollTop = timelineScroller.scrollHeight;
      }
    });
  });

  $effect(() => {
    const current = command;
    if (!autoMode || busy || !current) return;
    const next = nextAutoAction(current);
    if (!next) return;
    const key = `${current.command_id}:${current.status}:${next.action}`;
    if (key === autoActionKey) return;
    autoActionKey = key;
    queueMicrotask(() => {
      if (autoMode) void act(next.action, next.extra ?? {});
    });
  });

  async function refreshApps() {
    apps = await listApps();
  }

  function scheduleArtifactRefresh(id: string) {
    if (artifactRefreshTimer) return;
    artifactRefreshTimer = setTimeout(() => {
      artifactRefreshTimer = null;
      void refreshState(id).catch(() => undefined);
    }, 800);
  }

  async function loadApp(id: string) {
    loading = true;
    error = '';
    try {
      const [snapshot, document] = await Promise.all([getWorkspace(id), getArtifacts(id)]);
      events = snapshot.events;
      command = snapshot.command ?? null;
      deploymentPreferences = snapshot.deployment_preferences ?? null;
      currentStage = (command?.stage ?? snapshot.current_stage ?? 'requirements') as Stage;
      const loadedFileArtifacts = await loadFileArtifacts(id);
      applyArtifactSnapshot(document, loadedFileArtifacts, true);
      connect(id);
    } catch (reason) {
      error = errorMessage(reason);
    } finally {
      loading = false;
    }
  }

  function connect(id: string) {
    source?.close();
    const after = events.at(-1)?.event_id ?? 0;
    source = connectEvents(
      id,
      after,
      (event) => {
        connected = true;
        if (!events.some((item) => item.event_id === event.event_id)) events = [...events, event];
        if (event.kind !== 'progress') void refreshState(id);
        else if (event.stage === 'implementation') scheduleArtifactRefresh(id);
      },
      () => (connected = false)
    );
    source.onopen = () => (connected = true);
  }

  async function refreshState(id = appId) {
    if (!id) return;
    const [snapshot, document] = await Promise.all([getWorkspace(id), getArtifacts(id)]);
    const nextCommand = snapshot.command ?? null;
    command = nextCommand;
    deploymentPreferences = snapshot.deployment_preferences ?? null;
    currentStage = (command?.stage ?? snapshot.current_stage ?? currentStage) as Stage;
    let nextFileArtifacts = fileArtifacts;
    if (['implementation', 'testing'].includes(nextCommand?.stage ?? '') || Object.keys(fileArtifacts).length) {
      nextFileArtifacts = await loadFileArtifacts(id);
    }
    applyArtifactSnapshot(document, nextFileArtifacts);
    await refreshApps();
  }

  function artifactSnapshotSignatures(
    document: ArtifactDocument,
    files: Record<string, FileArtifactSnapshot>
  ) {
    const signatures: Record<string, string> = {};
    for (const [stage, value] of Object.entries(document.artifacts)) {
      if (!internalArtifactTypes.has(stage) && artifactPresent(value)) {
        signatures[stage] = JSON.stringify(value);
      }
    }
    for (const [stage, value] of Object.entries(files)) {
      signatures[stage] = `${value.version_no}:${value.files.map((file) => `${file.path}:${file.sha256}`).join('|')}`;
    }
    return signatures;
  }

  function applyArtifactSnapshot(
    document: ArtifactDocument,
    files: Record<string, FileArtifactSnapshot>,
    initial = false
  ) {
    const nextSignatures = artifactSnapshotSignatures(document, files);
    const nextStages = Object.keys(nextSignatures);
    const changedArtifacts = nextStages.filter(
      (stage) => nextSignatures[stage] && nextSignatures[stage] !== artifactSignatures[stage]
    );

    artifacts = document;
    fileArtifacts = files;
    artifactSignatures = nextSignatures;

    if (initial) {
      if (!nextSignatures[selectedArtifact]) {
        selectedArtifact = nextStages.at(-1) ?? selectedArtifact;
      }
      return;
    }

    const latestArtifact = changedArtifacts.at(-1);
    if (latestArtifact) {
      selectedArtifact = latestArtifact;
      if (window.innerWidth >= 900) artifactOpen = true;
    }
  }

  function reviewArtifact(stage: string) {
    selectedArtifact = stage;
    artifactOpen = true;
  }

  async function loadFileArtifacts(id: string) {
    const entries = await Promise.all(
      fileArtifactTypes.map(async (type) => {
        try {
          return [type, await getFileArtifact(id, type)] as const;
        } catch {
          return null;
        }
      })
    );
    return Object.fromEntries(entries.filter((entry) => entry !== null)) as Record<string, FileArtifactSnapshot>;
  }

  async function act(action: string, extra: Record<string, unknown> = {}) {
    if (!appId || busy) return;
    actionBusy = true;
    error = '';
    try {
      await sendCommand(appId, { action, ...extra });
      await refreshState();
    } catch (reason) {
      error = errorMessage(reason);
    } finally {
      actionBusy = false;
    }
  }

  async function send(text: string) {
    await act('message', {
      text,
      action_id: command?.status === 'AWAITING_INPUT' ? command.command_id : undefined,
      context: { stage: selectedStage, artifact_stage: selectedArtifact }
    });
  }

  async function saveCloudPreferences(preferences: DeploymentPreferences) {
    if (!appId || preferenceSaving) return;
    preferenceSaving = true;
    error = '';
    try {
      const response = await saveDeploymentPreferences(appId, preferences);
      deploymentPreferences = response.preferences;
      await refreshState();
    } catch (reason) {
      error = errorMessage(reason);
      throw reason;
    } finally {
      preferenceSaving = false;
    }
  }

  function toggleAutoMode() {
    autoMode = !autoMode;
    autoActionKey = '';
    window.localStorage.setItem(AUTO_MODE_STORAGE_KEY, String(autoMode));
  }

  function chooseApp(id: string) {
    if (id !== appId) {
      appId = id;
      void goto(`/workspace/?app=${id}`, { replaceState: false, noScroll: true });
    }
  }

  function trackTimelineScroll() {
    if (!timelineScroller) return;
    followTimeline =
      timelineScroller.scrollHeight - timelineScroller.scrollTop - timelineScroller.clientHeight < 96;
  }
</script>

<svelte:head><title>EasyDep · Development workspace</title></svelte:head>

<div class="flex h-dvh min-h-0 overflow-hidden bg-[#f4f4f0]">
  <AppSidebar
    {apps}
    currentAppId={appId}
    collapsed={sidebarCollapsed}
    onSelect={chooseApp}
    onNew={() => goto('/')}
    onToggle={() => (sidebarCollapsed = !sidebarCollapsed)}
  />

  <main class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    <header class="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-[#deded7] bg-white/90 px-5 backdrop-blur">
      <div class="min-w-0">
        <h1 class="truncate text-sm font-semibold">{apps.find((app) => app.app_id === appId)?.title ?? 'Development workspace'}</h1>
        <div class="mt-1 flex items-center gap-2 text-[10px] text-[#85877e]">
          {#if connected}<Wifi size={11} class="text-[#2d7354]" /> Live{:else}<WifiOff size={11} /> Reconnecting{/if}
          <span>·</span><span class="font-mono">{appId.slice(0, 8)}</span>
        </div>
      </div>
      <div class="hidden md:block"><StageRail current={currentStage} {command} /></div>
      <div class="flex items-center gap-2">
        <Badge tone={command?.status === 'FAILED' ? 'danger' : busy ? 'warning' : 'success'}>{command?.status ?? 'READY'}</Badge>
        <Button size="icon" variant="ghost" onclick={() => refreshState()} aria-label="Refresh"><RefreshCw size={15} /></Button>
        <Button size="icon" variant="ghost" onclick={() => (artifactOpen = !artifactOpen)} aria-label="Toggle artifact panel">
          {#if artifactOpen}<PanelRightClose size={17} />{:else}<PanelRightOpen size={17} />{/if}
        </Button>
      </div>
    </header>

    {#if !appId}
      <div class="flex flex-1 items-center justify-center text-sm text-[#777970]">Select an application on the left or start a new one.</div>
    {:else}
      <div class="relative grid min-h-0 flex-1 overflow-hidden {artifactOpen ? 'grid-cols-[minmax(440px,1fr)_minmax(400px,42%)]' : 'grid-cols-1'}">
        <section class="flex min-h-0 flex-col overflow-hidden bg-[#f7f7f4]">
          <div
            class="scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain"
            bind:this={timelineScroller}
            onscroll={trackTimelineScroll}
          >
            {#if loading}
              <div class="mt-24 text-center text-sm text-[#85877e]">Loading workspace history…</div>
            {:else}
              <ChatTimeline
                {appId}
                {events}
                document={artifacts}
                {fileArtifacts}
                regions={cloudRegions}
                showDeploymentPreferences={currentStage === 'requirements' && !deploymentPreferences}
                preferenceSaving={preferenceSaving}
                onDeploymentPreferencesSave={saveCloudPreferences}
                onArtifactSelect={reviewArtifact}
              />
            {/if}
          </div>
          {#if error}<div class="mx-auto mb-2 w-full max-w-3xl px-5 text-xs text-[#a24037]">{error}</div>{/if}
          <Composer
            {command}
            {busy}
            {autoMode}
            context={{ stage: selectedStage, artifact_stage: selectedArtifact }}
            onSend={send}
            onAction={act}
            onToggleAutoMode={toggleAutoMode}
          />
        </section>
        {#if artifactOpen}
          <ArtifactPane
            {appId}
            document={artifacts}
            {fileArtifacts}
            {events}
            selected={selectedArtifact}
            onSelect={reviewArtifact}
            onClose={() => (artifactOpen = false)}
          />
        {/if}
      </div>
    {/if}
  </main>
</div>
