<script lang="ts">
	import { mapView, snapshot } from "$lib/stores/data.js";
	import {
		VIEW_META,
		ANOMALY_STOPS,
		TEMP_STOPS,
		FEELS_STOPS,
		cssGradient,
	} from "$lib/utils/scales.js";

	const tempGradient = cssGradient(TEMP_STOPS);
	const feelsGradient = cssGradient(FEELS_STOPS);
	const anomalyGradient = cssGradient(ANOMALY_STOPS);

	// The ICON-EU run the data is based on. This is the model's *initialisation*
	// time, not when we fetched — DWD publishes each run ~3 h after its nominal
	// hour, so the newest available run always trails wall-clock by several hours.
	function formatModelRun(ts: string | null | undefined): string | null {
		if (!ts) return null;
		const d = new Date(ts);
		const hh = d.getUTCHours().toString().padStart(2, "0");
		const mm = d.getUTCMinutes().toString().padStart(2, "0");
		return `${hh}:${mm} UTC`;
	}

	// When our cron actually ingested the data — the real "freshness" signal.
	function formatFetched(ts: string | null | undefined): string | null {
		if (!ts) return null;
		const then = new Date(ts).getTime();
		if (Number.isNaN(then)) return null;
		const mins = Math.round((now - then) / 60000);
		if (mins < 1) return "just now";
		if (mins < 60) return `${mins} min ago`;
		const hrs = Math.round(mins / 60);
		if (hrs < 24) return `${hrs} h ago`;
		return `${Math.round(hrs / 24)} d ago`;
	}

	// Re-tick every minute so the relative "updated" label stays current.
	let now = $state(Date.now());
	$effect(() => {
		const id = setInterval(() => (now = Date.now()), 60000);
		return () => clearInterval(id);
	});

	let modelRun = $derived(formatModelRun($snapshot?.modelRunTime));
	// formatFetched reads `now` internally, so this re-derives every minute tick.
	let fetched = $derived(formatFetched($snapshot?.fetchedAt));
</script>

<div
	class="absolute bottom-3 right-3 z-10 w-56 bg-[rgba(8,8,16,0.9)] backdrop-blur-xl border border-white/10 rounded-xl p-3 text-white/85 shadow-lg shadow-black/50"
>
	<p class="text-[11px] uppercase tracking-widest text-white/55 font-semibold mb-2">
		{VIEW_META[$mapView].label}
	</p>

	{#if $mapView === "difference"}
		<div class="h-2 rounded" style="background:{anomalyGradient}"></div>
		<div class="flex justify-between text-[10px] text-white/55 mt-1">
			<span>−8°C</span>
			<span>0</span>
			<span>+8°C</span>
		</div>
		<p class="text-[10px] text-white/50 mt-1.5 leading-snug">
			vs 1961–1990 climate baseline (≈1°C cooler than today's normal)
		</p>
	{:else}
		<div
			class="h-2 rounded"
			style="background:{$mapView === 'apparent_temperature' ? feelsGradient : tempGradient}"
		></div>
		<div class="flex justify-between text-[10px] text-white/55 mt-1">
			<span>&lt;0°C</span>
			<span>22°C</span>
			<span>45°C+</span>
		</div>
	{/if}

	<div class="mt-2.5 pt-2.5 border-t border-white/[0.08] flex items-start gap-2">
		<span class="inline-block w-2.5 h-2.5 rounded-full bg-white/30 mt-0.5 shrink-0"></span>
		<span class="text-[11px] text-white/55 leading-snug">Dot size = residents in that grid cell</span>
	</div>

	{#if modelRun}
		<p class="text-[11px] text-white/50 mt-2 leading-snug">
			DWD ICON-EU · {modelRun} run{#if fetched}<br />updated {fetched}{/if}
		</p>
	{/if}
</div>
