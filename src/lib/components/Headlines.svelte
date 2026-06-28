<script lang="ts">
	import {
		snapshot,
		selectedCountry,
		selectedCountryData,
		headlineThreshold,
		clientAffected,
		selectedDate,
		error,
		loadData,
	} from "$lib/stores/data.js";
	import { getCountryName } from "$lib/countries.js";

	// Compact "1.2 million" style for the population headlines.
	function millions(n: number | undefined | null): string {
		if (n == null) return "–";
		if (n >= 1_000_000) {
			const m = n / 1_000_000;
			return `${m >= 10 ? m.toFixed(0) : m.toFixed(1)} million`;
		}
		if (n >= 1_000) return `${(n / 1_000).toFixed(0)},000`;
		return n.toLocaleString("en");
	}

	// Headline values switch between Europe-wide (snapshot) and the selected
	// country. They are fixed metrics — independent of the active map view.
	let place = $derived(
		$selectedCountry ? getCountryName($selectedCountry) : "Europe",
	);

	let anomaly = $derived(
		$selectedCountry
			? ($selectedCountryData?.avgAnomalyC ?? null)
			: ($snapshot?.meanAnomalyC ?? null),
	);
	let popAboveAvg = $derived(
		$selectedCountry
			? ($selectedCountryData?.popAboveAvg ?? null)
			: ($snapshot?.popAboveAvg ?? null),
	);
	// clientAffected uses the grid's per-cell daily-max temperature, so it
	// updates instantly as the slider moves. Falls back to the server figure
	// before gridData has loaded.
	let affected = $derived(
		$clientAffected ??
		($selectedCountry
			? ($selectedCountryData?.affected ?? null)
			: ($snapshot?.totalAffected ?? null)),
	);
	let reference = $derived($snapshot?.referencePeriod ?? "1961–1990");

	let hasData = $derived($snapshot != null);
	let warmer = $derived(anomaly != null && anomaly >= 0);
	// The first two headlines compare against the E-OBS 1961–1990 climatology.
	// When that reference data isn't loaded (e.g. the .npz isn't on the server),
	// meanAnomalyC/avgAnomalyC come back null — hide those lines rather than
	// rendering "–" / "0", and show a short note instead.
	let climAvailable = $derived(anomaly != null);
	// popAboveAvg === null means clim data wasn't available for this date range
	// (distinct from a genuine 0). Hide the "uncommonly hot" line in that case.
	let hasPopAboveAvg = $derived(popAboveAvg != null);

	let dateHeading = $derived(
		$selectedDate === 'yesterday' ? 'Yesterday' : $selectedDate === 'tomorrow' ? 'Tomorrow' : 'Today'
	);

	// Past vs. present tense:
	//   yesterday → always past tense
	//   tomorrow  → always present/forecast
	//   today     → past tense from 19:00 CET onward
	let pastTense = $derived.by(() => {
		if ($selectedDate === 'yesterday') return true;
		if ($selectedDate === 'tomorrow')  return false;
		void $snapshot; // re-evaluate on each data refresh
		try {
			const h = Number(
				new Intl.DateTimeFormat("en-GB", {
					hour: "numeric",
					hour12: false,
					timeZone: "Europe/Berlin",
				}).format(new Date()),
			);
			return h >= 19;
		} catch {
			return false;
		}
	});

	// Methodology copy for the info popovers, keyed by headline index.
	// Item 2 is derived so it reflects the current headline threshold.
	const INFO_STATIC = [
		{
			title: "Difference from the historic average",
			body: `The area-mean difference between today's daily-mean temperature and the long-term average for this calendar date. For each ~6 km grid cell we average the day's hourly ICON-EU temperatures and subtract the 1961–1990 daily-mean from the E-OBS observational climatology. The figure shown is the unweighted mean across all populated grid cells.

We use the 1961–1990 baseline — the WMO/IPCC reference for climate change — on purpose: it shows the full warming signal. It runs roughly 1 °C cooler than the present-day (1991–2020) normal, so a figure near 0 °C here is actually slightly below what's normal today, and our anomalies read about 1 °C warmer than tools on the modern baseline (e.g. Copernicus Climate Pulse). Today's value comes from a forecast model (ICON-EU) while the baseline is observational (E-OBS), so small model biases feed into it too — the spatial pattern on the map is more robust than the exact continental average.`,
		},
		{
			title: "People in uncommonly hot conditions",
			body: `The number of people in grid cells where today's daily-mean temperature exceeds the 90th percentile of daily means for this calendar date across 1961–1990 — hotter than all but the warmest 10% of comparable days in that reference period. Because the climate has warmed, clearing the 1961–1990 90th percentile happens more often now than it did then, so this is "uncommon" relative to the historic baseline rather than rare today.`,
		},
	];
	let INFO = $derived([
		...INFO_STATIC,
		{
			title: `People at ${$headlineThreshold} °C or more`,
			body: `The number of people in grid cells that reach at least ${$headlineThreshold} °C air temperature in any hourly snapshot today. Today blends the most recent analysis hours with forecast hours out to tonight. Temperatures come from the DWD ICON-EU model; population from the GHS-POP density raster.`,
		},
	]);

	let openInfo = $state<number | null>(null);
	let root = $state<HTMLDivElement>();

	function toggle(i: number, e: MouseEvent) {
		e.stopPropagation();
		openInfo = openInfo === i ? null : i;
	}
	function onWindowClick(e: MouseEvent) {
		if (openInfo !== null && root && !root.contains(e.target as Node))
			openInfo = null;
	}
	function onKeydown(e: KeyboardEvent) {
		if (e.key === "Escape") openInfo = null;
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKeydown} />

{#snippet info(i: number)}
	<button
		type="button"
		class="align-super ml-0.5 text-[0.55em] leading-none text-zinc-500 hover:text-zinc-200 transition-colors"
		aria-label="How is this number calculated?"
		aria-expanded={openInfo === i}
		onclick={(e) => toggle(i, e)}
	>
		<span
			class="inline-flex items-center justify-center w-4 h-4 rounded-full border border-current font-bold"
			>?</span
		>
	</button>
	{#if openInfo === i}
		<div
			class="absolute left-0 top-full mt-1.5 z-30 w-80 max-w-[calc(100vw-2rem)] bg-zinc-900 border border-zinc-700 rounded-xl p-3.5 shadow-2xl shadow-black/60 text-sm font-normal text-zinc-300 leading-snug"
			role="dialog"
		>
			<p class="font-semibold text-zinc-100 mb-1.5">{INFO[i].title}</p>
			<p class="whitespace-pre-line">{INFO[i].body}</p>
			<a
				href="/methodology"
				class="inline-block mt-2 text-xs text-amber-400 hover:text-amber-300 no-underline"
				>Full methodology →</a
			>
		</div>
	{/if}
{/snippet}

{#if $error && !hasData}
	<div class="flex flex-col gap-3">
		<p class="text-sm text-zinc-400">Could not load data. Check your connection or try again.</p>
		<button
			type="button"
			class="self-start text-xs text-amber-400 hover:text-amber-300 transition-colors underline-offset-2 hover:underline"
			onclick={() => loadData()}
		>Retry →</button>
	</div>
{:else if hasData}
	<div data-testid="headlines" bind:this={root}>
		<p class="text-2xl font-bold text-zinc-100 mb-2 tracking-tight">
			{dateHeading} in {place}…
		</p>
		<ul class="flex flex-col gap-1 text-2xl leading-tight">
			<li class="relative">
				<span class="text-zinc-600" aria-hidden="true">… </span><span class="text-zinc-500 font-normal">approx.</span> <strong class="font-bold text-red-400">{millions(affected)}</strong>
				people {pastTense ? "experienced" : "are experiencing"} temperatures of
				<strong class="font-semibold text-zinc-100">{$headlineThreshold}°C or more</strong
				>.{@render info(2)}
			</li>
			{#if climAvailable && hasPopAboveAvg}
				<li class="relative">
					<span class="text-zinc-600" aria-hidden="true">… </span><span class="text-zinc-500 font-normal">approx.</span> <strong class="font-bold text-amber-400">{millions(popAboveAvg)}</strong>
					people {pastTense ? "experienced" : "are experiencing"}
					<strong class="font-semibold text-zinc-100">uncommonly hot</strong>
					temperatures.{@render info(1)}
				</li>
			{/if}
			{#if climAvailable}
				<li class="relative">
					<span class="text-zinc-600" aria-hidden="true">… </span>
					temperatures {pastTense ? "were" : "are"}
					<span class="text-zinc-500 font-normal">approx.</span>
					<strong
						class="font-semibold {warmer
							? 'text-orange-400/90'
							: 'text-sky-400/90'}"
					>
						{anomaly! >= 0 ? "+" : ""}{anomaly!.toFixed(1)}°C
					</strong>
					{warmer ? "warmer" : "cooler"} than the {reference} average for this date.{@render info(
						0,
					)}
				</li>
			{:else}
				<li class="">
					<span class="text-zinc-600" aria-hidden="true">… </span>
					Comparison to the {reference} average is currently unavailable.
				</li>
			{/if}
		</ul>
	</div>
{:else}
	<div class="flex flex-col gap-2" aria-hidden="true">
		<div class="h-7 w-48 bg-zinc-800 rounded animate-pulse mb-1"></div>
		{#each Array(3) as _}
			<div class="h-6 w-full max-w-xl bg-zinc-800 rounded animate-pulse"></div>
		{/each}
	</div>
{/if}

