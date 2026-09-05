<script lang="ts">
  import { Check, LoaderCircle, Search, Server } from '@lucide/svelte';
  import { applyDeploymentSizing, getDeploymentSizing } from '$lib/api';
  import type { CapacityOverride, ComputeSizingUnit, DeploymentSizingResponse } from '$lib/types';
  import { errorMessage } from '$lib/utils';

  let {
    appId,
    targetId,
    onApplied
  }: {
    appId: string;
    targetId: string;
    onApplied?: () => void | Promise<void>;
  } = $props();

  type Selection = {
    computeUnitId: string;
    sku: string;
    replicaCount: number;
    replicationConfirmed: boolean;
  };

  let response = $state<DeploymentSizingResponse | null>(null);
  let selections = $state<Record<string, Selection>>({});
  let loading = $state(false);
  let saving = $state(false);
  let error = $state('');
  let loadedKey = '';
  let editing = $state(false);
  let hasSavedSizing = $derived(Boolean(response?.selected?.length));
  let capacityOverrides = $state<Record<string, CapacityOverride>>({});
  let capacityLoading = $state<Record<string, boolean>>({});

  function initialSelection(unit: ComputeSizingUnit, stored: DeploymentSizingResponse['selected']) {
    const previous = stored.find((item) => item.computeUnitId === unit.computeUnitId);
    return {
      computeUnitId: unit.computeUnitId,
      sku: previous?.sku ?? unit.candidates[0]?.sku ?? '',
      replicaCount: previous?.replicaCount ?? unit.minimumReplicaCount,
      replicationConfirmed: previous?.replicationConfirmed ?? false
    };
  }

  function needsCapacity(unit: ComputeSizingUnit) {
    return !unit.minimumRequirements.minVCpu ||
      !unit.minimumRequirements.minMemoryGiB ||
      unit.candidates.length === 0;
  }

  function capacityValue(unit: ComputeSizingUnit, field: 'minVCpu' | 'minMemoryGiB') {
    return capacityOverrides[unit.computeUnitId]?.[field] ?? unit.minimumRequirements[field] ?? '';
  }

  function updateCapacity(unit: ComputeSizingUnit, field: 'minVCpu' | 'minMemoryGiB', value: number) {
    const current = capacityOverrides[unit.computeUnitId] ?? {
      computeUnitId: unit.computeUnitId,
      minVCpu: unit.minimumRequirements.minVCpu ?? 1,
      minMemoryGiB: unit.minimumRequirements.minMemoryGiB ?? 1
    };
    capacityOverrides = {
      ...capacityOverrides,
      [unit.computeUnitId]: { ...current, [field]: value }
    };
  }

  function validCapacity(unit: ComputeSizingUnit) {
    const override = capacityOverrides[unit.computeUnitId];
    return Boolean(override && override.minVCpu > 0 && override.minMemoryGiB > 0);
  }

  function initialCapacity(result: DeploymentSizingResponse) {
    const fallback = result.guidance.computeUnits
      .filter((unit) => needsCapacity(unit))
      .filter((unit) =>
        (unit.minimumRequirements.minVCpu ?? 0) > 0 &&
        (unit.minimumRequirements.minMemoryGiB ?? 0) > 0
      )
      .map((unit) => ({
        computeUnitId: unit.computeUnitId,
        minVCpu: unit.minimumRequirements.minVCpu as number,
        minMemoryGiB: unit.minimumRequirements.minMemoryGiB as number
      }));
    return Object.fromEntries(
      [...fallback, ...(result.capacityOverrides ?? [])].map((item) => [
        item.computeUnitId,
        item
      ])
    );
  }

  async function findVmSize(unit: ComputeSizingUnit) {
    if (!validCapacity(unit) || capacityLoading[unit.computeUnitId]) return;
    capacityLoading = { ...capacityLoading, [unit.computeUnitId]: true };
    error = '';
    try {
      const result = await getDeploymentSizing(appId, targetId, Object.values(capacityOverrides));
      if (`${appId}:${targetId}` !== loadedKey) return;
      response = result;
      selections = Object.fromEntries(
        result.guidance.computeUnits.map((candidate) => [
          candidate.computeUnitId,
          initialSelection(candidate, result.selected)
        ])
      );
      capacityOverrides = initialCapacity(result);
    } catch (reason) {
      error = errorMessage(reason);
    } finally {
      capacityLoading = { ...capacityLoading, [unit.computeUnitId]: false };
    }
  }

  $effect(() => {
    const key = `${appId}:${targetId}`;
    if (!appId || !targetId || loadedKey === key) return;
    loadedKey = key;
    editing = false;
    capacityOverrides = {};
    capacityLoading = {};
    loading = true;
    error = '';
    getDeploymentSizing(appId, targetId)
      .then((result) => {
        if (`${appId}:${targetId}` !== loadedKey) return;
        response = result;
        selections = Object.fromEntries(
          result.guidance.computeUnits.map((unit) => [
            unit.computeUnitId,
            initialSelection(unit, result.selected)
          ])
        );
        capacityOverrides = initialCapacity(result);
      })
      .catch((reason) => {
        if (`${appId}:${targetId}` === loadedKey) error = errorMessage(reason);
      })
      .finally(() => {
        if (`${appId}:${targetId}` === loadedKey) loading = false;
      });
  });

  function update(unitId: string, values: Partial<Selection>) {
    selections = {
      ...selections,
      [unitId]: { ...selections[unitId], ...values }
    };
  }

  function monthlyTotal() {
    return (response?.guidance.computeUnits ?? []).reduce((total, unit) => {
      const selection = selections[unit.computeUnitId];
      const candidate = unit.candidates.find((item) => item.sku === selection?.sku);
      return total + (candidate?.hourlyComputeUSD ?? 0) * 730 * (selection?.replicaCount ?? 0);
    }, 0);
  }

  function savedMonthlyTotal() {
    return (response?.selected ?? []).reduce((total, selection) => {
      const unit = response?.guidance.computeUnits.find(
        (candidate) => candidate.computeUnitId === selection.computeUnitId
      );
      const candidate = unit?.candidates.find((item) => item.sku === selection.sku);
      return total + (candidate?.hourlyComputeUSD ?? 0) * 730 * selection.replicaCount;
    }, 0);
  }

  function canApply() {
    const units = response?.guidance.computeUnits ?? [];
    return units.length > 0 && units.every((unit) => {
      const selection = selections[unit.computeUnitId];
      const replicationAccepted =
        unit.replicationSafety !== 'unknown' ||
        (selection?.replicaCount ?? 0) <= 1 ||
        Boolean(selection?.replicationConfirmed);
      return Boolean(selection?.sku) &&
        (selection?.replicaCount ?? 0) >= unit.minimumReplicaCount &&
        replicationAccepted;
    });
  }

  async function apply() {
    if (!canApply() || saving) return;
    saving = true;
    error = '';
    try {
      await applyDeploymentSizing(appId, {
        targetId,
        structureDigest: response?.structureDigest ?? '',
        selections: Object.values(selections),
        capacityOverrides: Object.values(capacityOverrides)
      });
      await onApplied?.();
      // Re-read this target before showing the compact saved state. The GET
      // response is the source of truth for whether sizing was persisted.
      loadedKey = '';
    } catch (reason) {
      error = errorMessage(reason);
    } finally {
      saving = false;
    }
  }
</script>

<section class="mb-3 rounded-lg border border-[#dce2dc] bg-[#f8faf7] p-3" aria-label="VM sizing">
  <header class="flex items-start gap-2">
    <Server class="mt-0.5 shrink-0 text-[#477058]" size={15} />
    <div class="min-w-0 flex-1">
      <strong class="block text-xs text-[#315641]">VM size and replicas</strong>
      <p class="mt-0.5 text-[10px] leading-4 text-[#737970]">Choose values for this target. Prices cover VM compute only.</p>
    </div>
  </header>

  {#if loading}
    <p class="mt-3 flex items-center gap-2 text-[11px] text-[#737970]"><LoaderCircle class="animate-spin" size={13} /> Loading VM candidates…</p>
  {:else if response}
    {#if hasSavedSizing && !editing}
      <div class="mt-3 rounded-md border border-[#e0e4de] bg-white p-2.5">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <strong class="block text-[11px] text-[#315641]">Saved deployment configuration</strong>
            <p class="mt-1 text-[10px] text-[#737970]">{response.target.provider.toUpperCase()} · {response.target.region}</p>
          </div>
          <span class="shrink-0 text-[10px] font-semibold text-[#477058]">${savedMonthlyTotal().toFixed(2)}/month</span>
        </div>
        <div class="mt-2 space-y-1.5">
          {#each response.selected as selection}
            <div class="flex items-center justify-between gap-2 rounded-md bg-[#f6f8f4] px-2 py-1.5 text-[10px] text-[#59645b]">
              <span class="min-w-0 truncate"><strong>{selection.computeUnitId}</strong> · {selection.sku}</span>
              <span class="shrink-0">{selection.replicaCount} replica{selection.replicaCount === 1 ? '' : 's'}</span>
            </div>
          {/each}
        </div>
        <button
          type="button"
          class="focus-ring mt-3 w-full rounded-md border border-[#b7cbbd] bg-white px-2.5 py-1.5 text-[10px] font-semibold text-[#315f46] hover:bg-[#f1f7f2]"
          onclick={() => (editing = true)}
        >
          Change deployment configuration
        </button>
      </div>
    {:else}
    <div class="mt-3 space-y-2">
      {#each response.guidance.computeUnits as unit}
        <div class="rounded-md border border-[#e0e4de] bg-white p-2.5">
          <div class="flex items-center justify-between gap-2">
            <strong class="truncate text-[11px]">{unit.computeUnitId}</strong>
            <span class="text-[10px] text-[#7b8078]">min {unit.minimumRequirements.minVCpu ?? '?'} vCPU · {unit.minimumRequirements.minMemoryGiB ?? '?'} GiB</span>
          </div>
          {#if needsCapacity(unit)}
            <div class="mt-2 rounded-md border border-[#eadfc7] bg-[#fffaf0] p-2">
              <p class="text-[10px] leading-4 text-[#80612e]">Enter positive minimum capacity to find VM sizes for this unit.</p>
              <div class="mt-2 grid grid-cols-2 gap-2">
                <label class="text-[10px] text-[#6f6045]">vCPU
                  <input
                    class="focus-ring mt-1 w-full rounded-md border border-[#dfd2b8] bg-white px-2 py-1.5 text-[11px]"
                    type="number" min="0.1" step="0.1"
                    value={capacityValue(unit, 'minVCpu')}
                    aria-label={`Minimum vCPU for ${unit.computeUnitId}`}
                    oninput={(event) => updateCapacity(unit, 'minVCpu', event.currentTarget.valueAsNumber)}
                  />
                </label>
                <label class="text-[10px] text-[#6f6045]">Memory GiB
                  <input
                    class="focus-ring mt-1 w-full rounded-md border border-[#dfd2b8] bg-white px-2 py-1.5 text-[11px]"
                    type="number" min="0.1" step="0.1"
                    value={capacityValue(unit, 'minMemoryGiB')}
                    aria-label={`Minimum memory GiB for ${unit.computeUnitId}`}
                    oninput={(event) => updateCapacity(unit, 'minMemoryGiB', event.currentTarget.valueAsNumber)}
                  />
                </label>
              </div>
              <button
                type="button"
                class="focus-ring mt-2 flex items-center gap-1 rounded-md bg-[#8a672d] px-2.5 py-1.5 text-[10px] font-semibold text-white disabled:opacity-50"
                disabled={!validCapacity(unit) || capacityLoading[unit.computeUnitId]}
                onclick={() => findVmSize(unit)}
              >
                {#if capacityLoading[unit.computeUnitId]}<LoaderCircle class="animate-spin" size={11} />{:else}<Search size={11} />{/if}
                Find VM sizes
              </button>
            </div>
          {/if}
          {#if unit.candidates.length}
            <div class="mt-2 grid grid-cols-[minmax(0,1fr)_5rem] gap-2">
              <select
                class="focus-ring min-w-0 rounded-md border border-[#d8ddd7] bg-white px-2 py-1.5 text-[11px]"
                value={selections[unit.computeUnitId]?.sku ?? ''}
                onchange={(event) => update(unit.computeUnitId, { sku: event.currentTarget.value })}
              >
                {#each unit.candidates as candidate}
                  <option value={candidate.sku}>{candidate.sku} · {candidate.vCPU} vCPU · {candidate.memoryGiB} GiB · ${candidate.monthlyComputeUSD.toFixed(2)}/mo</option>
                {/each}
              </select>
              <input
                class="focus-ring min-w-0 rounded-md border border-[#d8ddd7] px-2 py-1.5 text-[11px]"
                type="number"
                min={unit.minimumReplicaCount}
                aria-label={`Replica count for ${unit.computeUnitId}`}
                value={selections[unit.computeUnitId]?.replicaCount ?? unit.minimumReplicaCount}
                oninput={(event) => update(unit.computeUnitId, { replicaCount: Number(event.currentTarget.value) })}
              />
            </div>
            {#if unit.replicationSafety === 'unknown' && (selections[unit.computeUnitId]?.replicaCount ?? 1) > 1}
              <label class="mt-2 flex items-start gap-2 text-[10px] leading-4 text-[#696e67]">
                <input
                  class="mt-0.5"
                  type="checkbox"
                  checked={selections[unit.computeUnitId]?.replicationConfirmed ?? false}
                  onchange={(event) => update(unit.computeUnitId, { replicationConfirmed: event.currentTarget.checked })}
                />
                Every replica can handle the same request; sessions and business data are not kept only in one container.
              </label>
            {/if}
          {:else}
            <p class="mt-2 text-[10px] leading-4 text-[#8a5f2c]">{unit.reason ?? 'No VM candidate matches the current capacity input.'}</p>
          {/if}
        </div>
      {/each}
    </div>
    <footer class="mt-3 flex items-center justify-between gap-3 border-t border-[#e1e5df] pt-2.5">
      <span class="text-[10px] text-[#6f746d]">Estimated compute: ${monthlyTotal().toFixed(2)}/month</span>
      <button
        class="focus-ring flex items-center gap-1 rounded-md bg-[#2d6b4d] px-2.5 py-1.5 text-[10px] font-semibold text-white disabled:opacity-50"
        disabled={!canApply() || saving}
        onclick={apply}
      >
        {#if saving}<LoaderCircle class="animate-spin" size={11} />{:else}<Check size={11} />{/if}
        Apply
      </button>
    </footer>
    {/if}
  {/if}
  {#if error}<p class="mt-2 text-[10px] leading-4 text-[#a24037]">{error}</p>{/if}
</section>
