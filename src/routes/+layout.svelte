<script lang="ts">
	import "../app.css";
	import { onMount } from "svelte";
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { browser } from "$app/environment";
	import HeatMap from "$lib/components/HeatMap.svelte";
	import MapViewSwitch from "$lib/components/MapViewSwitch.svelte";
	import MapLegend from "$lib/components/MapLegend.svelte";
	import Hero from "$lib/components/Hero.svelte";
	import WelcomePopup from "$lib/components/WelcomePopup.svelte";
	import { loadData, selectedCountry, isMapResizing, headlineThreshold, setHeadlineThreshold, selectedDate } from "$lib/stores/data.js";
	import { getCountryCode } from "$lib/countries.js";

	let { children } = $props();

// Width of the hero rail in the XL split layout. Stored as a CSS length and
	// applied only at the xl breakpoint (see the style media query), so smaller
	// screens keep the full-width stacked layout. Persisted across visits.
	let railWidth = $state("33%");
	let dragging = $state(false);

	function startDrag(e: PointerEvent) {
		dragging = true;
		isMapResizing.set(true);
		(e.target as HTMLElement).setPointerCapture?.(e.pointerId);
		e.preventDefault();
	}
	function onDrag(e: PointerEvent) {
		if (!dragging) return;
		const min = 300;
		const max = Math.min(window.innerWidth * 0.6, window.innerWidth - 360);
		railWidth = `${Math.round(Math.max(min, Math.min(max, e.clientX)))}px`;
	}
	function endDrag() {
		if (!dragging) return;
		dragging = false;
		isMapResizing.set(false);
		try {
			localStorage.setItem("hero-rail-width", railWidth);
		} catch {}
	}

	// Keep the selected-country store in sync with the URL so /spain etc. remain
	// shareable and the headlines/map reflect the route.
	$effect(() => {
		const route = $page.url.pathname;
		if (route === "/methodology") return;
		const segments = route.split("/").filter(Boolean);
		if (segments.length === 1) {
			const code = getCountryCode(segments[0]);
			if (code) {
				selectedCountry.set(code);
				return;
			}
		}
		selectedCountry.set(null);
	});

	// Sync ?threshold=N URL param when headlineThreshold changes.
	// Only runs in the browser; only writes when the URL actually needs updating.
	$effect(() => {
		const thr = $headlineThreshold;
		if (!browser) return;
		const url = new URL(window.location.href);
		if (thr === 30) url.searchParams.delete("threshold");
		else url.searchParams.set("threshold", String(thr));
		const newSearch = url.search;
		if (newSearch !== window.location.search) {
			goto(url.pathname + newSearch, { replaceState: true, noScroll: true, keepFocus: true });
		}
	});

	onMount(() => {
		// Initialise threshold from URL before the first data load.
		const urlThr = Number($page.url.searchParams.get("threshold"));
		if (urlThr >= 25 && urlThr <= 45) setHeadlineThreshold(urlThr);

		loadData();
		try {
			const saved = localStorage.getItem("hero-rail-width");
			if (saved) railWidth = saved;
		} catch {}

		// Reload data when threshold or selected date changes (skip first fire).
		let firstFire = true;
		const reload = () => { if (firstFire) { firstFire = false; return; } loadData(); };
		const unsubThr  = headlineThreshold.subscribe(reload);
		const unsubDate = selectedDate.subscribe(reload);

		// Refresh periodically — the pipeline regenerates the static files every 3h.
		const interval = setInterval(() => loadData(), 5 * 60 * 1000);
		return () => {
			unsubThr();
			unsubDate();
			clearInterval(interval);
		};
	});
</script>

<svelte:head>
	<title>European Heat Tracker</title>
	<meta name="description" content="Real-time tracking of extreme heat exposure across Europe" />
</svelte:head>

{#if $page.url.pathname === "/methodology"}
	{@render children()}
{:else}
	<div class="flex flex-col min-h-screen" class:select-none={dragging}>
		<!-- First screen: stacked on smaller screens (hero above map), split into
		     a resizable hero rail + map on extra-large screens. The rail keeps its
		     natural height (never cut off); the map is capped at 80vh and sticks to
		     the top while a taller rail scrolls past. -->
		<div class="flex flex-col xl:flex-row xl:items-start">
			<div class="hero-rail shrink-0 w-full" style="--rail-w:{railWidth}">
				<Hero />
			</div>

			<!-- Drag handle (XL only) -->
			<button
				type="button"
				aria-label="Resize panels"
				class="hidden xl:flex shrink-0 self-start w-1.5 items-center justify-center cursor-col-resize bg-transparent border-0 group sticky top-0 h-[80vh]"
				onpointerdown={startDrag}
			>
				<span class="w-px h-10 rounded bg-white/15 group-hover:bg-white/40 {dragging ? 'bg-white/50' : ''} transition-colors"></span>
			</button>

			<div
				id="map"
				class="map-col relative overflow-hidden w-full h-[80vh] xl:flex-1 xl:sticky xl:top-0 xl:self-start transition-[filter] duration-150"
				style={dragging ? "filter: blur(2px)" : ""}
			>
				<HeatMap />
				<MapViewSwitch />
				<MapLegend />
			</div>
		</div>

		{@render children()}
	</div>
	<WelcomePopup />
{/if}

<svelte:window onpointermove={onDrag} onpointerup={endDrag} />

<style>
	/* The rail width only applies in the XL split layout; below xl the columns
	   stack full-width (the inline --rail-w is ignored). */
	@media (min-width: 1280px) {
		.hero-rail {
			width: var(--rail-w);
		}
	}
</style>
