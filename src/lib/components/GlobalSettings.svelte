<script lang="ts">
	import { headlineThreshold, setHeadlineThreshold, selectedDate } from "$lib/stores/data.js";
	import type { SelectedDate } from "$lib/stores/data.js";
	import { trackEvent } from "$lib/analytics.js";

	const DEFAULT = 30;
	const MIN = 25;
	const MAX = 45;

	function localDateStr(offsetDays: number): string {
		const now = new Date();
		const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offsetDays);
		return `${d.getDate()}.${d.getMonth() + 1}.`;
	}

	const DATE_OPTIONS: { value: SelectedDate; label: string; offset: number }[] = [
		{ value: 'yesterday', label: 'Yesterday', offset: -1 },
		{ value: 'today',     label: 'Today',     offset:  0 },
		{ value: 'tomorrow',  label: 'Tomorrow',  offset:  1 },
	];

	let open = $state(false);
	let root: HTMLDivElement;

	function onWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}
	function onKeydown(e: KeyboardEvent) {
		if (e.key === "Escape") open = false;
	}

	let selectedOpt = $derived(DATE_OPTIONS.find((d) => d.value === $selectedDate) ?? DATE_OPTIONS[1]);
	let dateLabel = $derived(`${selectedOpt.label} ${localDateStr(selectedOpt.offset)}`);
	let isDefault = $derived($headlineThreshold === DEFAULT && $selectedDate === 'today');
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKeydown} />

<div class="relative" bind:this={root}>
	<button
		type="button"
		class="flex items-center gap-2 h-8 px-2.5 bg-zinc-950 border rounded-lg text-zinc-400 hover:text-zinc-100 transition-colors {open
			? 'border-zinc-500 text-zinc-100'
			: 'border-zinc-700 hover:border-zinc-500'}"
		aria-label="Settings"
		aria-expanded={open}
		onclick={() => { open = !open; if (!open) return; trackEvent('settings_open'); }}
	>
		<!-- Threshold + date indicators -->
		<span class="text-xs font-medium tabular-nums leading-none {isDefault ? 'text-zinc-400' : 'text-zinc-300'}">
			{$headlineThreshold}°C · {dateLabel}
		</span>
		<!-- Cog icon -->
		<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="shrink-0">
			<circle cx="12" cy="12" r="3" />
			<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
		</svg>
	</button>

	{#if open}
		<div
			class="absolute top-full right-0 mt-2 z-30 w-72 bg-zinc-950 border border-zinc-700 rounded-xl shadow-2xl shadow-black/60 p-4"
		>
			<!-- Date selector -->
			<p class="text-sm font-semibold text-zinc-100 mb-3">Date</p>
			<div class="flex gap-1 bg-zinc-900 rounded-lg p-1">
				{#each DATE_OPTIONS as opt}
					<button
						type="button"
						class="flex-1 flex flex-col items-center py-1.5 rounded-md font-medium transition-colors {$selectedDate === opt.value
							? 'bg-zinc-700 text-zinc-100'
							: 'text-zinc-400 hover:text-zinc-200'}"
						onclick={() => { selectedDate.set(opt.value); }}
					>
						<span class="text-xs leading-tight">{opt.label}</span>
						<span class="text-[10px] leading-tight opacity-60">{localDateStr(opt.offset)}</span>
					</button>
				{/each}
			</div>
			<p class="text-xs text-zinc-400 mt-2 leading-snug">
				Days are UTC (midnight to midnight). Tomorrow shows forecast data.
			</p>

			<!-- Threshold -->
			<div class="flex items-center justify-between mt-4 mb-3 pt-4 border-t border-zinc-800">
				<p class="text-sm font-semibold text-zinc-100">Heat threshold</p>
				{#if $headlineThreshold !== DEFAULT}
					<button
						class="text-xs text-amber-400 hover:text-amber-300 transition-colors"
						onclick={() => setHeadlineThreshold(DEFAULT)}
					>Reset to {DEFAULT}°C</button>
				{/if}
			</div>

			<div class="flex items-center gap-3">
				<input
					type="range"
					min={MIN}
					max={MAX}
					step="1"
					value={$headlineThreshold}
					oninput={(e) => setHeadlineThreshold(Number((e.target as HTMLInputElement).value))}
					class="flex-1 accent-amber-500 cursor-pointer"
					aria-label="Heat threshold in degrees Celsius"
				/>
				<span class="text-sm font-semibold text-zinc-100 tabular-nums w-10 text-right">{$headlineThreshold}°C</span>
			</div>

			<p class="text-xs text-zinc-400 mt-2 leading-snug">
				Grid cells at or above this temperature are highlighted on the map and counted in the headline figure.
			</p>
		</div>
	{/if}
</div>
