<script lang="ts">
	import CountrySelect from "$lib/components/CountrySelect.svelte";
	import GlobalSettings from "$lib/components/GlobalSettings.svelte";
	import Headlines from "$lib/components/Headlines.svelte";
	import DownloadButton from "$lib/components/DownloadButton.svelte";
</script>

<section>
	<div class="px-4 py-8 max-w-4xl mx-auto">
		<!-- Brand -->
		<div class="flex items-center gap-2 mb-6">
			<a
				href="https://klimadashboard.org"
				target="_blank"
				rel="noopener"
				title="Klimadashboard"
			>
				<img
					class="w-6 h-6 rounded"
					src="/logo-klimadashboard.svg"
					alt="Klimadashboard"
				/>
			</a>
			<span class="font-bold text-sm">European Heat Tracker</span>
			<span
				class="bg-amber-600/10 border border-amber-600 text-amber-600 rounded px-1 py-0.5 text-xs uppercase tracking-wide font-bold"
				>Beta</span
			>
		</div>

		<!-- Headline figures -->
		<Headlines />

		<p class="text-base leading-snug max-w-2xl mt-7 text-zinc-300">
			Heatwaves are becoming <em
				class="underline not-italic underline-offset-3 decoration-amber-600 decoration-2"
				>more frequent and more deadly</em
			>
			across Europe. Updated every 3 hours, this tool overlays the latest weather
			observations and forecasts with a European population grid to estimate
			<em
				class="underline not-italic underline-offset-3 decoration-amber-600 decoration-2"
				>how many people are currently exposed to dangerous heat</em
			>.
		</p>

		<p class="mt-4 text-sm leading-snug max-w-2xl text-zinc-400">
			This tool is in beta as we continue to validate and gather feedback.
			Figures may change as new forecasts arrive throughout the day, and likely
			underestimate heat in cities (urban heat islands) and mountainous regions.
		</p>

		<!-- Controls row -->
		<div
			class="mt-4 pt-5 border-t border-zinc-800 flex items-center gap-2 flex-wrap"
		>
			<CountrySelect />
			<GlobalSettings />
			<DownloadButton />
		</div>
	</div>
</section>
