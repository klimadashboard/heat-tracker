<script lang="ts">
	import { snapshot } from "$lib/stores/data.js";
	import { browser } from "$app/environment";

	const STORAGE_KEY = "heat-tracker-welcome-seen";
	const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000;

	let show = $state(false);

	$effect(() => {
		if (!browser) return;
		try {
			const seen = localStorage.getItem(STORAGE_KEY);
			if (!seen || Date.now() - Number(seen) > THREE_DAYS_MS) {
				show = true;
			}
		} catch {
			// localStorage unavailable
		}
	});

	function dismiss() {
		try {
			localStorage.setItem(STORAGE_KEY, String(Date.now()));
		} catch {}
		show = false;
	}

	let totalMillions = $derived(
		$snapshot?.totalPopulation ? Math.round($snapshot.totalPopulation / 1_000_000) : null
	);
</script>

{#if show}
	<!-- Backdrop -->
	<div
		class="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
		role="dialog"
		aria-modal="true"
		aria-label="Welcome to the European Heat Tracker"
	>
		<div
			class="relative bg-zinc-950 border border-zinc-700 rounded-2xl shadow-2xl shadow-black/70 max-w-lg w-full max-h-[90vh] overflow-y-auto p-6"
		>
			<!-- Close button -->
			<button
				type="button"
				onclick={dismiss}
				class="absolute top-4 right-4 w-7 h-7 flex items-center justify-center rounded-full text-zinc-400 hover:text-zinc-100 hover:bg-white/10 transition-colors"
				aria-label="Close"
			>
				<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
					<path d="M1 1l10 10M11 1L1 11" />
				</svg>
			</button>

			<!-- Header -->
			<div class="flex items-center gap-2 mb-1">
				<a href="https://klimadashboard.org" target="_blank" rel="noopener">
					<img class="w-5 h-5 rounded" src="/logo-klimadashboard.svg" alt="Klimadashboard" />
				</a>
				<span class="font-bold text-sm text-zinc-100">heat-tracker.eu</span>
				<span class="bg-amber-600/10 border border-amber-600 text-amber-600 rounded px-1 py-0.5 text-xs uppercase tracking-wide font-bold">Beta</span>
			</div>

			<h1 class="text-2xl font-bold text-zinc-100 mt-3 mb-1 leading-tight">
				Tracking Heat Exposure for {totalMillions != null ? `${totalMillions} Million` : 'Millions of'} Europeans
			</h1>

			<div class="space-y-3 mt-4 text-sm text-zinc-300 leading-relaxed">
				<p>
					Combining live weather data, historical measurements and a European population grid,
					the European Heat Tracker visualises the human impact of heat across Europe.
				</p>

				<p class="text-zinc-400">
					Due to the nature of the weather model, we likely underestimate affected people in
					dense urban areas and in very mountainous regions.
				</p>

				<p class="text-zinc-400">
					Our tool is in beta, our code is open source and we welcome feedback from the
					community on how to improve.
					<a
						href="https://github.com/klimadashboard/heat-tracker"
						target="_blank"
						rel="noopener"
						class="text-zinc-300 hover:text-white underline underline-offset-2"
					>View source on GitHub.</a>
				</p>
			</div>

			<!-- Footer + CTA -->
			<div class="mt-5 pt-4 border-t border-zinc-800">
				<p class="text-sm text-zinc-400 mb-4">
					A tool by the non-profit
					<a href="https://klimadashboard.org" target="_blank" rel="noopener" class="text-[#28a889] hover:text-[#3dbfa0] font-medium">Klimadashboard.org</a>.
					<a href="https://klimadashboard.org/donate" target="_blank" rel="noopener" class="text-[#28a889] hover:text-[#3dbfa0] font-medium">Donate now.</a>
				</p>
				<button
					type="button"
					onclick={dismiss}
					class="w-full py-2.5 bg-amber-600 hover:bg-amber-500 text-white font-semibold rounded-lg transition-colors text-sm"
				>
					Start exploring
				</button>
			</div>
		</div>
	</div>
{/if}
