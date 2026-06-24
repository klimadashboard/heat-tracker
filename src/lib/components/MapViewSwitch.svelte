<script lang="ts">
	import { mapView } from "$lib/stores/data.js";
	import type { MapView } from "$lib/stores/data.js";
	import { VIEW_ORDER, VIEW_META } from "$lib/utils/scales.js";

	let open = $state(false);
	let root: HTMLDivElement;

	const COMPARISON_VIEWS = VIEW_ORDER.filter((v) => v === "difference");
	const TODAY_VIEWS = VIEW_ORDER.filter((v) => v !== "difference");

	const DESCRIPTIONS: Record<MapView, string> = {
		difference:
			"How much hotter or colder each area is today compared with its 1961–1990 average for this calendar date (E-OBS climatology).",
		temperature: "Today's peak 2 m air temperature — the standard, universal measure.",
		apparent_temperature:
			"Feels-like temperature (Steadman) — adds the effect of humidity and wind.",
	};

	function onWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}
	function onKeydown(e: KeyboardEvent) {
		if (e.key === "Escape") open = false;
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKeydown} />

{#snippet viewButton(view: MapView)}
	<button
		type="button"
		role="tab"
		aria-selected={$mapView === view}
		class="px-3 py-1.5 rounded-full text-xs sm:text-sm font-semibold whitespace-nowrap transition-colors {$mapView ===
		view
			? 'bg-white/90 text-zinc-900'
			: 'text-white/60 hover:text-white hover:bg-white/10'}"
		onclick={() => mapView.set(view)}
	>
		{VIEW_META[view].label}
	</button>
{/snippet}

<div
	class="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 max-w-[calc(100%-1.5rem)]"
	bind:this={root}
>
	<!-- View selector -->
	<div
		class="flex items-center gap-0.5 bg-[rgba(8,8,16,0.9)] backdrop-blur-xl border border-white/10 rounded-full p-1 shadow-lg shadow-black/50 overflow-x-auto"
		role="tablist"
		aria-label="Map view"
	>
		{#each COMPARISON_VIEWS as view}
			{@render viewButton(view)}
		{/each}
		<div class="self-stretch w-px bg-white/15 mx-1 shrink-0" aria-hidden="true"></div>
		{#each TODAY_VIEWS as view}
			{@render viewButton(view)}
		{/each}
	</div>

	<!-- Info cog -->
	<div class="bg-[rgba(8,8,16,0.9)] backdrop-blur-xl border border-white/10 rounded-full p-1 shadow-lg shadow-black/50 shrink-0">
		<button
			type="button"
			class="w-7 h-7 flex items-center justify-center rounded-full text-white/70 hover:text-white hover:bg-white/10 transition-colors {open
				? 'bg-white/10 text-white'
				: ''}"
			aria-label="Map view info"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
				<circle cx="12" cy="12" r="10" />
				<line x1="12" y1="8" x2="12" y2="8" stroke-width="2.5" stroke-linecap="round" />
				<polyline points="11 12 12 12 12 16" stroke-width="2" />
			</svg>
		</button>
	</div>

	{#if open}
		<div
			class="absolute top-full right-0 mt-2 w-[300px] max-w-[calc(100vw-1.5rem)] bg-zinc-950 border border-zinc-700 rounded-xl shadow-2xl shadow-black/60 p-3.5 text-left"
		>
			<p class="text-xs uppercase tracking-widest text-zinc-500 font-semibold mb-1">
				Compared to history
			</p>
			<div class="flex flex-col gap-2">
				{#each COMPARISON_VIEWS as view}
					<div class="{$mapView === view ? 'opacity-100' : 'opacity-55'}">
						<p class="text-sm font-semibold text-zinc-100">{VIEW_META[view].label}</p>
						<p class="text-xs text-zinc-400 leading-snug">{DESCRIPTIONS[view]}</p>
					</div>
				{/each}
			</div>

			<p class="text-xs uppercase tracking-widest text-zinc-500 font-semibold mt-3 mb-1">
				Today's conditions
			</p>
			<div class="flex flex-col gap-2">
				{#each TODAY_VIEWS as view}
					<div class="{$mapView === view ? 'opacity-100' : 'opacity-55'}">
						<p class="text-sm font-semibold text-zinc-100">{VIEW_META[view].label}</p>
						<p class="text-xs text-zinc-400 leading-snug">{DESCRIPTIONS[view]}</p>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
