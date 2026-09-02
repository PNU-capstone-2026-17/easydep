<script lang="ts">
  import { Check, LoaderCircle, Server } from '@lucide/svelte';
  import { applyDeploymentSizing, getDeploymentSizing } from '$lib/api';
  import type { ComputeSizingUnit, DeploymentSizingResponse } from '$lib/types';
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

  function initialSelection(unit: ComputeSizingUnit, stored: DeploymentSizingResponse['selected']) {
    const previous = stored.find((item) => item.computeUnitId === unit.computeUnitId);
    return {
      computeUnitId: unit.computeUnitId,
      sku: previous?.sku ?? unit.candidates[0]?.sku ?? '',
      replicaCount: previous?.replicaCount ?? unit.minimumReplicaCount,
      replicationConfirmed: previous?.replicationConfirmed ?? false
    };
  }

  $effect(() => {
    const key = `${appId}:${targetId}`;
    if (!appId || !targetId || loadedKey === key) return;
    loadedKey = key;
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

  function canApply() {
    const units = response?.guidance.computeUnits ?? [];
    return units.length > 0 && units.every((unit) => {
      const selection = selections[unit.computeUnitId];
      return Boolean(selection?.sku) && selection.replicaCount >= unit.minimumReplicaCount;
    });
  }

  async function apply() {
    if (!canApply() || saving) return;
    saving = true;
    error = '';
    try {
      await applyDeploymentSizing(appId, {
        targetId,
        selections: Object.values(selections)
      });
      await onApplied?.();
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
    <div class="mt-3 space-y-2">
      {#each response.guidance.computeUnits as unit}
        <div class="rounded-md border border-[#e0e4de] bg-white p-2.5">
          <div class="flex items-center justify-between gap-2">
            <strong class="truncate text-[11px]">{unit.computeUnitId}</strong>
            <span class="text-[10px] text-[#7b8078]">min {unit.minimumRequirements.minVCpu ?? '?'} vCPU · {unit.minimumRequirements.minMemoryGiB ?? '?'} GiB</span>
          </div>
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
  {#if error}<p class="mt-2 text-[10px] leading-4 text-[#a24037]">{error}</p>{/if}
</section>
