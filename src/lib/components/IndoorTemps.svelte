<script lang="ts">
	import { onMount } from 'svelte';

	type Submission = { temperature: number; submitted_at: number };

	// Deterministic seeded RNG for stable simulation data
	function lcg(seed: number) {
		let s = seed;
		return () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 0) / 0xffffffff; };
	}
	function simulatedEntries(): Submission[] {
		const rng = lcg(42);
		const now = Math.floor(Date.now() / 1000);
		return Array.from({ length: 100 }, (_, i) => {
			// Box-Muller: normal distribution, mean 28°C, std 4°C
			const u = Math.max(0.0001, rng()), v = rng();
			const raw = 28 + 4 * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
			return {
				temperature: Math.max(20, Math.min(40, Math.round(raw * 2) / 2)),
				submitted_at: now - Math.floor(rng() * 86400),
			};
		});
	}

	// State
	let sliderValue = $state(25);
	let submitted = $state(false);
	let submitting = $state(false);
	let submitError = $state('');
	let submissions = $state<Submission[]>(simulatedEntries()); // replaced by real data on mount

	// Color scale matching the map's tempScale exactly
	const colorStops: [number, [number, number, number]][] = [
		[-15, [0x1e, 0x4d, 0x9e]],
		[-5,  [0x3d, 0x80, 0xc0]],
		[0,   [0x5a, 0xae, 0xd8]],
		[5,   [0x7e, 0xc8, 0xe3]],
		[10,  [0x98, 0xd4, 0xa0]],
		[15,  [0xb5, 0xd8, 0x6c]],
		[20,  [0xdc, 0xc4, 0x3c]],
		[25,  [0xf0, 0xa0, 0x20]],
		[30,  [0xe8, 0x70, 0x20]],
		[35,  [0xd4, 0x3d, 0x1a]],
		[40,  [0xb0, 0x15, 0x15]],
		[45,  [0x7a, 0x00, 0x00]],
	];

	function tempColor(temp: number): string {
		for (let i = 0; i < colorStops.length - 1; i++) {
			const [t0, c0] = colorStops[i];
			const [t1, c1] = colorStops[i + 1];
			if (temp <= t1) {
				const t = Math.max(0, (temp - t0) / (t1 - t0));
				return `rgb(${Math.round(c0[0] + t * (c1[0] - c0[0]))},${Math.round(c0[1] + t * (c1[1] - c0[1]))},${Math.round(c0[2] + t * (c1[2] - c0[2]))})`;
			}
		}
		return '#7a0000';
	}

	// Beeswarm chart
	let chartWidth = $state(0);
	const ML = 8, MR = 8;
	const DOT_R = 5;
	const ROW_H = 12;
	const PAD = 8;
	const LABEL_H = 18;

	function tempToX(temp: number): number {
		return ML + ((temp - 20) / 20) * (chartWidth - ML - MR);
	}

	const beeswarm = $derived.by(() => {
		if (chartWidth === 0) return { dots: [], axisY: 50, svgH: 70 };

		const placed: { sx: number; row: number }[] = [];
		const sorted = [...submissions]
			.filter(s => s.temperature >= 19 && s.temperature <= 41)
			.sort((a, b) => a.temperature - b.temperature);

		const result: { sx: number; row: number; temp: number }[] = [];

		for (const s of sorted) {
			const sx = tempToX(s.temperature);
			let placed_row = 0;
			// Try rows outward from center: 0, +1, -1, +2, -2, ...
			for (let i = 0; i <= 60; i++) {
				const row = i === 0 ? 0 : i % 2 === 1 ? Math.ceil(i / 2) : -(i / 2);
				const fits = placed.every(p => {
					const dx = sx - p.sx;
					const dy = (row - p.row) * ROW_H;
					return dx * dx + dy * dy >= (DOT_R * 2 + 1) ** 2;
				});
				if (fits) { placed_row = row; break; }
			}
			placed.push({ sx, row: placed_row });
			result.push({ sx, row: placed_row, temp: s.temperature });
		}

		const maxUp   = result.length > 0 ? Math.max(0, ...result.map(d =>  d.row)) : 0;
		const maxDown = result.length > 0 ? Math.max(0, ...result.map(d => -d.row)) : 0;
		const axisY = PAD + DOT_R + maxUp * ROW_H;
		const svgH = axisY + DOT_R + maxDown * ROW_H + DOT_R + 4 + LABEL_H;

		return {
			dots: result.map(d => ({
				sx: d.sx,
				sy: axisY - d.row * ROW_H,
				temp: d.temp,
			})),
			axisY,
			svgH,
		};
	});

	// Slider thumb positioning: calc(pct% - offset_px) to align with native thumb
	const sliderPct = $derived(((sliderValue - 20) / 20) * 100);
	const thumbLeft = $derived(`calc(${sliderPct}% - ${sliderPct * 0.2 - 10}px)`);

	// Gradient track colors at the slider range (20–40°C)
	const trackGradient = `linear-gradient(to right, ${tempColor(20)}, ${tempColor(25)}, ${tempColor(30)}, ${tempColor(35)}, ${tempColor(40)})`;

	// X axis labels for beeswarm
	const xLabels = [20, 25, 30, 35, 40];

	async function loadData() {
		try {
			const res = await fetch('/api/indoor-temps');
			if (res.ok) submissions = await res.json();
		} catch {}
	}

	async function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		submitError = '';
		submitting = true;
		try {
			const res = await fetch('/api/indoor-temps', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ temperature: sliderValue }),
			});
			if (res.ok) {
				submitted = true;
				await loadData();
			} else {
				submitError = 'Could not submit. Please try again.';
			}
		} catch {
			submitError = 'Could not submit. Please try again.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		loadData();
		const iv = setInterval(loadData, 60_000);
		return () => clearInterval(iv);
	});
</script>

<!-- Title -->
<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
	How hot is it indoors near you?
</div>

<div class="flex flex-col lg:flex-row gap-6 flex-1">

	<!-- Form -->
	<div class="lg:w-56 shrink-0 flex flex-col gap-3">
		<p class="text-zinc-300 text-sm leading-relaxed">
			Buildings without <strong class="text-zinc-100">air conditioning</strong> or proper insulation
			trap heat — indoor temperatures can exceed outdoor readings by 5°C or more during a heatwave.
		</p>

		{#if submitted}
			<div class="text-sm text-zinc-200 bg-zinc-800 rounded-lg px-3 py-2 border border-zinc-700">
				✓ Submitted — your reading is on the chart.
			</div>
		{:else}
			<form onsubmit={handleSubmit} class="flex flex-col gap-3">
				<!-- Temperature display -->
				<div class="flex items-baseline gap-2">
					<span class="text-4xl font-bold font-condensed leading-none transition-colors"
						style="color: {tempColor(sliderValue)}">{sliderValue}°C</span>
					<span class="text-zinc-400 text-sm">indoors</span>
				</div>

				<!-- Slider -->
				<div>
					<div class="relative h-8 flex items-center">
						<!-- Gradient track — inset by 10px each side to align with the native thumb travel range -->
						<div class="absolute h-1.5 rounded-full" style="left:10px;right:10px;background:{trackGradient}"></div>
						<!-- Native input (invisible, handles interaction) -->
						<input
							type="range" min="20" max="40" step="0.5"
							bind:value={sliderValue}
							class="absolute inset-0 w-full cursor-pointer opacity-0"
							style="margin: 0; height: 100%"
							aria-label="Indoor temperature"
						/>
						<!-- Custom thumb -->
						<div
							class="absolute w-5 h-5 rounded-full pointer-events-none border-2 border-zinc-600 shadow-lg"
							style="left: {thumbLeft}; background: white"
						></div>
					</div>
					<!-- Scale labels -->
					<div class="flex justify-between text-zinc-500 text-xs mt-0.5 px-0.5">
						{#each xLabels as t}
							<span>{t}°</span>
						{/each}
					</div>
				</div>

				{#if submitError}
					<p class="text-red-400 text-xs">{submitError}</p>
				{/if}

				<button
					type="submit"
					disabled={submitting}
					class="bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-zinc-100 text-sm rounded-lg px-3 py-1.5 transition-colors text-left font-sans border-0 cursor-pointer"
				>
					{submitting ? 'Submitting…' : 'Submit →'}
				</button>
			</form>
		{/if}
	</div>

	<!-- Beeswarm chart -->
	<div class="flex-1 flex flex-col gap-1 min-w-0">
		<div class="flex justify-between items-baseline">
			<p class="text-zinc-400 text-xs uppercase tracking-widest">Community readings — last 24 h</p>
			{#if submissions.length > 0}
				<p class="text-zinc-500 text-xs">{submissions.length} {submissions.length === 1 ? 'reading' : 'readings'}</p>
			{/if}
		</div>

		<div bind:clientWidth={chartWidth} class="w-full">
			{#if chartWidth > 0}
				{@const { dots, axisY, svgH } = beeswarm}
				<svg width={chartWidth} height={svgH} aria-label="Beeswarm chart of indoor temperature submissions">
					<!-- Axis line -->
					<line x1={ML} y1={axisY} x2={chartWidth - MR} y2={axisY} stroke="#52525b" stroke-width="1" />

					<!-- X axis labels -->
					{#each xLabels as t}
						{@const x = tempToX(t)}
						<text x={x} y={axisY + LABEL_H - 4} text-anchor="middle" fill="#71717a" font-size="10">{t}°</text>
					{/each}

					<!-- Empty state -->
					{#if dots.length === 0}
						<text x={(ML + chartWidth - MR) / 2} y={axisY - 20} text-anchor="middle" fill="#52525b" font-size="12">
							No readings yet — be the first!
						</text>
					{/if}

					<!-- Dots -->
					{#each dots as d}
						<circle cx={d.sx} cy={d.sy} r={DOT_R} fill={tempColor(d.temp)} opacity="0.9" />
					{/each}
				</svg>
			{/if}
		</div>
	</div>

</div>
