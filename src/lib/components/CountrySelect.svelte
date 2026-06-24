<script lang="ts">
	import { goto } from "$app/navigation";
	import { tick } from "svelte";
	import { countries, selectedCountry, headlineThreshold } from "$lib/stores/data.js";
	import { getCountryName, CODE_TO_SLUG } from "$lib/countries.js";
	import { ANOMALY_STOPS, cssGradient, interpColor } from "$lib/utils/scales.js";
	import { formatPopulation } from "$lib/utils/format.js";

	let open = $state(false);
	let query = $state("");
	// -1: nothing highlighted (focus stays in search), 0: "All of Europe", 1..N: filtered[i-1]
	let activeIdx = $state(-1);

	let root: HTMLDivElement;
	let searchInput: HTMLInputElement;
	let scrollPane: HTMLDivElement;

	const anomalyGradient = cssGradient(ANOMALY_STOPS);
	const BAR_MIN = -8, BAR_MAX = 8;

	function barPct(anomaly: number | null | undefined): number | null {
		if (anomaly == null) return null;
		const clamped = Math.max(BAR_MIN, Math.min(BAR_MAX, anomaly));
		return ((clamped - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100;
	}

	let sorted = $derived(
		[...$countries].sort((a, b) => {
			if (b.affected !== a.affected) return b.affected - a.affected;
			return (b.avgAnomalyC ?? -99) - (a.avgAnomalyC ?? -99);
		}),
	);
	let filtered = $derived(
		query.trim()
			? sorted.filter((c) =>
					getCountryName(c.country).toLowerCase().includes(query.trim().toLowerCase()),
				)
			: sorted,
	);
	let total = $derived(1 + filtered.length); // "All of Europe" + countries
	let currentLabel = $derived($selectedCountry ? getCountryName($selectedCountry) : "All of Europe");

	function optId(i: number) { return `csel-opt-${i}`; }

	function choose(code: string | null) {
		open = false;
		query = "";
		activeIdx = -1;
		if (code === null) {
			if ($selectedCountry) goto("/");
		} else {
			const slug = CODE_TO_SLUG[code];
			if (slug) goto(`/${slug}`);
		}
	}

	async function openMenu() {
		open = true;
		activeIdx = -1;
		await tick();
		searchInput?.focus();
	}

	function closeMenu() {
		open = false;
		activeIdx = -1;
	}

	function scrollActive() {
		if (activeIdx < 0 || !scrollPane) return;
		document.getElementById(optId(activeIdx))?.scrollIntoView({ block: "nearest" });
	}

	function handleSearchKey(e: KeyboardEvent) {
		switch (e.key) {
			case "ArrowDown":
				e.preventDefault();
				if (activeIdx < total - 1) { activeIdx++; scrollActive(); }
				break;
			case "ArrowUp":
				e.preventDefault();
				if (activeIdx > 0) { activeIdx--; scrollActive(); }
				break;
			case "Enter":
				if (activeIdx >= 0) {
					e.preventDefault();
					activeIdx === 0 ? choose(null) : choose(filtered[activeIdx - 1].country);
				} else if (filtered.length === 1 && query.trim()) {
					choose(filtered[0].country);
				}
				break;
			case "Escape":
				e.stopPropagation();
				closeMenu();
				break;
			case "Tab":
				closeMenu();
				break;
		}
	}

	function onWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) closeMenu();
	}
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === "Escape" && open) closeMenu();
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKeydown} />

<div class="relative inline-flex items-center gap-1 text-left" bind:this={root}>
	<!-- Trigger button -->
	<button
		type="button"
		class="flex items-center gap-2 h-8 bg-zinc-950 border border-zinc-700 hover:border-zinc-500 rounded-lg px-2.5 text-left transition-colors min-w-[180px]"
		aria-haspopup="listbox"
		aria-expanded={open}
		aria-controls="csel-listbox"
		onclick={() => (open ? closeMenu() : openMenu())}
	>
		<span class="text-[10px] uppercase tracking-widest text-zinc-500">Showing</span>
		<span class="text-sm font-semibold text-zinc-100 flex-1">{currentLabel}</span>
		<span class="text-zinc-500 text-xs transition-transform {open ? 'rotate-180' : ''}">▾</span>
	</button>

	<!-- Clear selection — only shown when a country is active -->
	{#if $selectedCountry}
		<button
			type="button"
			aria-label="Clear country selection, show all of Europe"
			onclick={() => choose(null)}
			class="w-6 h-6 flex items-center justify-center rounded-full text-zinc-500 hover:text-zinc-100 hover:bg-white/10 transition-colors shrink-0"
		>
			<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
				<path d="M1 1l8 8M9 1l-8 8"/>
			</svg>
		</button>
	{/if}

	<!-- Dropdown panel -->
	{#if open}
		<div
			class="absolute top-full left-0 z-30 mt-2 w-[340px] max-h-[60vh] overflow-hidden flex flex-col bg-zinc-950 border border-zinc-700 rounded-xl shadow-2xl shadow-black/60"
		>
			<!-- Search input (ARIA combobox) -->
			<div class="p-2 border-b border-zinc-800 shrink-0">
				<input
					bind:this={searchInput}
					type="text"
					value={query}
					oninput={(e) => { query = (e.target as HTMLInputElement).value; activeIdx = -1; }}
					placeholder="Search country…"
					autocomplete="off"
					role="combobox"
					aria-expanded="true"
					aria-controls="csel-listbox"
					aria-autocomplete="list"
					aria-activedescendant={activeIdx >= 0 ? optId(activeIdx) : undefined}
					class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
					onkeydown={handleSearchKey}
				/>
			</div>

			<!-- Column headers (decorative) -->
			<div
				class="flex items-center gap-3 px-3 py-1.5 border-b border-zinc-800 text-[10px] uppercase tracking-wider text-zinc-500 shrink-0"
				aria-hidden="true"
			>
				<span class="w-32 shrink-0">Country</span>
				<span class="flex-1 min-w-0"></span>
				<span class="w-12 text-right shrink-0">vs avg</span>
				<span class="w-12 text-right shrink-0">≥{$headlineThreshold}°C</span>
			</div>

			<!-- Options listbox -->
			<div
				id="csel-listbox"
				role="listbox"
				aria-label="Select region"
				class="overflow-y-auto py-1"
				bind:this={scrollPane}
			>
				<!-- "All of Europe" -->
				<button
					id={optId(0)}
					type="button"
					role="option"
					aria-selected={!$selectedCountry}
					class="w-full flex items-center gap-3 px-3 py-2 text-left transition-colors
						{!$selectedCountry ? 'bg-white/[0.06]' : ''}
						{activeIdx === 0 ? 'bg-white/10 ring-1 ring-inset ring-white/20' : 'hover:bg-white/5'}"
					onclick={() => choose(null)}
				>
					<span class="text-sm font-semibold text-zinc-100 flex-1">All of Europe</span>
				</button>

				{#each filtered as c, i (c.country)}
					{@const pct = barPct(c.avgAnomalyC)}
					{@const idx = i + 1}
					<button
						id={optId(idx)}
						type="button"
						role="option"
						aria-selected={$selectedCountry === c.country}
						class="w-full flex items-center gap-3 px-3 py-2 text-left transition-colors
							{$selectedCountry === c.country ? 'bg-white/[0.06]' : ''}
							{activeIdx === idx ? 'bg-white/10 ring-1 ring-inset ring-white/20' : 'hover:bg-white/5'}"
						onclick={() => choose(c.country)}
					>
						<span class="text-sm text-zinc-200 w-32 shrink-0 truncate">{getCountryName(c.country)}</span>
						<!-- Anomaly mini-bar -->
						<span class="flex-1 min-w-0">
							<span
								class="block h-1.5 rounded-full relative"
								style="background: {anomalyGradient}; opacity:0.35"
							>
								{#if pct !== null}
									<span
										class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2 h-2 rounded-full border border-black/40"
										style="left:{pct}%; background:{interpColor(ANOMALY_STOPS, c.avgAnomalyC ?? 0)}"
									></span>
								{/if}
							</span>
						</span>
						<span class="text-xs tabular-nums w-12 text-right shrink-0">
							{#if c.avgAnomalyC != null}
								<span class={c.avgAnomalyC >= 0 ? "text-orange-400" : "text-sky-400"}>
									{c.avgAnomalyC >= 0 ? "+" : ""}{c.avgAnomalyC.toFixed(1)}°
								</span>
							{:else}
								<span class="text-zinc-500">–</span>
							{/if}
						</span>
						<span class="text-xs w-12 text-right shrink-0 {c.affected > 0 ? 'text-red-400' : 'text-zinc-500'}">
							{c.affected > 0 ? formatPopulation(c.affected) : "—"}
						</span>
					</button>
				{/each}

				{#if filtered.length === 0}
					<p class="px-3 py-4 text-sm text-zinc-400 text-center" role="status">No countries match.</p>
				{/if}
			</div>
		</div>
	{/if}
</div>
