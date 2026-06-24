<script lang="ts">
	import type { Snippet } from "svelte";
</script>

{#snippet card(inner: Snippet, extraClass: string = "")}
	<div
		class="bg-zinc-900 rounded-xl border border-zinc-800 shadow-sm p-4 h-full relative leading-tight flex flex-col break-words hyphens-auto transition {extraClass}"
	>
		{@render inner()}
	</div>
{/snippet}

<section
	class="info-section bg-black py-12"
	aria-label="Background information"
>
	<div class="px-6">
		<div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
			<!-- 2024 heat mortality -->
			{#snippet c_deaths_2024()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-2 text-zinc-200">
					Heat mortality in Europe 2024
				</div>
				<p class="text-amber-400 text-5xl font-light font-condensed leading-none mt-1">
					62,775
				</p>
				<p class="text-zinc-200 text-sm mt-2 flex-1">
					estimated heat-related deaths across 32 European countries during
					summer 2024 (June to September)
				</p>
				<a
					href="https://www.nature.com/articles/s41591-025-03954-7"
					target="_blank"
					rel="noopener"
					class="text-xs leading-none mt-auto pt-3 text-zinc-400 no-underline hover:text-zinc-200 block"
				>
					Ballester et al., 2025 — <em>Nature Medicine</em> →
				</a>
			{/snippet}
			{@render card(c_deaths_2024, "")}

			<!-- Global temperature 2024 -->
			{#snippet c_temperature()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-2 text-zinc-200">
					Global temperature 2024
				</div>
				<p class="text-orange-400 text-5xl font-light font-condensed leading-none mt-1">
					+1.60°C
				</p>
				<p class="text-zinc-200 text-sm mt-2 flex-1">
					above pre-industrial average. 2024 was the first full calendar year
					to exceed the 1.5°C threshold set by the Paris Agreement.
				</p>
				<a
					href="https://climate.copernicus.eu/global-climate-highlights-2024"
					target="_blank"
					rel="noopener"
					class="text-xs leading-none mt-auto pt-3 text-zinc-400 no-underline hover:text-zinc-200 block"
				>
					Copernicus Climate Change Service, 2025 →
				</a>
			{/snippet}
			{@render card(c_temperature, "")}

			<!-- Data sources -->
			{#snippet c_sources()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					Data sources
				</div>
				<div class="flex flex-col gap-3 flex-1 text-sm">
					<div>
						<a href="https://opendata.dwd.de/weather/nwp/icon-eu/" target="_blank" rel="noopener" class="text-zinc-100 font-semibold hover:text-white hover:underline underline-offset-2">DWD ICON-EU</a>
						<p class="text-zinc-400 mt-0.5 leading-snug">Live weather model at ~6.5 km resolution, Deutscher Wetterdienst. Updated every 3 hours with hourly forecasts.</p>
					</div>
					<div>
						<a href="https://ghsl.jrc.ec.europa.eu/ghs_pop2023.php" target="_blank" rel="noopener" class="text-zinc-100 font-semibold hover:text-white hover:underline underline-offset-2">GHS-POP R2023A</a>
						<p class="text-zinc-400 mt-0.5 leading-snug">Global population grid at ~1 km², 2020 baseline. European Commission Joint Research Centre.</p>
					</div>
					<div>
						<a href="https://www.ecad.eu/download/ensembles/download.php" target="_blank" rel="noopener" class="text-zinc-100 font-semibold hover:text-white hover:underline underline-offset-2">E-OBS</a>
						<p class="text-zinc-400 mt-0.5 leading-snug">Gridded daily observations for Europe, 1961 to 1990 baseline. ECA&amp;D / Copernicus.</p>
					</div>
				</div>
				<p class="text-xs text-zinc-400 mt-auto pt-3">
					<a href="https://github.com/klimadashboard/heat-tracker" target="_blank" rel="noopener" class="hover:text-zinc-300 underline-offset-2 hover:underline">Open source on GitHub</a>
				</p>
			{/snippet}
			{@render card(c_sources, "")}

			<!-- Donation cell — full colour, sits next to data sources -->
			<div class="bg-[#1a6d51] rounded-xl p-4 h-full flex flex-col leading-tight">
				<div class="font-bold border-b border-white/20 pb-1 mb-3 text-white">
					Support our work
				</div>
				<p class="text-white text-sm leading-snug flex-1">
					Building new tools and visualisations needs your support. If you find the European Heat Tracker useful, please consider donating to Klimadashboard.
				</p>
				<a
					href="https://klimadashboard.org/donate"
					target="_blank"
					rel="noopener"
					class="mt-4 inline-flex items-center justify-center gap-1.5 px-4 py-2.5 bg-white hover:bg-white/90 text-[#1a6d51] font-semibold rounded-lg text-sm transition-colors no-underline"
				>
					Donate now →
				</a>
			</div>

			<!-- How we count (full width) -->
			{#snippet c_methodology()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					How we count affected people
				</div>
				<div class="grid grid-cols-1 sm:grid-cols-3 gap-4 flex-1">
					<div>
						<p class="text-zinc-100 text-sm font-bold mb-1">Weather model</p>
						<p class="text-zinc-300 text-sm leading-relaxed">
							<strong class="text-zinc-100">DWD ICON-EU</strong> publishes 8 model
							runs per day (every 3 hours, 00Z to 21Z). Each run provides
							hour-by-hour forecasts; the pipeline combines them to give continuous
							hourly coverage across the day.
						</p>
					</div>
					<div>
						<p class="text-zinc-100 text-sm font-bold mb-1">Population grid</p>
						<p class="text-zinc-300 text-sm leading-relaxed">
							<strong class="text-zinc-100">GHS-POP R2023A</strong> (EU Joint
							Research Centre) at ~1 km² resolution, 2020 baseline. Aggregated to
							~175,000 grid cells at ~6 to 7 km spacing, aligned with the weather
							model grid.
						</p>
					</div>
					<div>
						<p class="text-zinc-100 text-sm font-bold mb-1">Counting method</p>
						<p class="text-zinc-300 text-sm leading-relaxed">
							A person is counted as "affected" if their grid cell exceeds the
							chosen threshold in <em>any</em> hourly snapshot during the day. The
							anomaly and "uncommonly hot" figures use each cell's daily mean,
							compared against the 1961 to 1990 90th-percentile climatology from
							E-OBS.
						</p>
					</div>
				</div>
				<p class="text-xs leading-none mt-auto pt-3 text-zinc-400">
					Weather: opendata.dwd.de · Population: ghsl.jrc.ec.europa.eu ·
					Climatology: E-OBS (ECA&amp;D / Copernicus)
				</p>
			{/snippet}
			{@render card(c_methodology, "col-span-1 sm:col-span-2 xl:col-span-4")}

			<!-- Two indicators -->
			{#snippet c_indicators()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					Two heat indicators
				</div>
				<div class="flex flex-col gap-3 flex-1">
					<div>
						<p class="text-zinc-100 text-sm font-bold">Air temperature</p>
						<p class="text-zinc-300 text-sm leading-relaxed">
							Standard 2 m air temperature (°C). Simple and universal. Default
							threshold: 30°C.
						</p>
					</div>
					<div>
						<p class="text-zinc-100 text-sm font-bold">Feels Like</p>
						<p class="text-zinc-300 text-sm leading-relaxed">
							Steadman (1994) apparent temperature accounts for humidity and
							wind speed. Default threshold: 30°C.
						</p>
					</div>
				</div>
			{/snippet}
			{@render card(c_indicators, "")}

			<!-- How to use -->
			{#snippet c_howto()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					How to use this tool
				</div>
				<div class="flex flex-col gap-2.5 flex-1">
					<div class="flex gap-2 items-start">
						<span class="text-[#28a889] text-xs shrink-0 mt-0.5">→</span>
						<p class="text-zinc-300 text-sm leading-snug">
							Use the <strong class="text-zinc-100">country selector</strong> at
							the bottom to focus headline figures on any country
						</p>
					</div>
					<div class="flex gap-2 items-start">
						<span class="text-[#28a889] text-xs shrink-0 mt-0.5">→</span>
						<p class="text-zinc-300 text-sm leading-snug">
							Switch the <strong class="text-zinc-100">map view</strong> at
							the top of the map between difference from average, temperature,
							or feels like
						</p>
					</div>
					<div class="flex gap-2 items-start">
						<span class="text-[#28a889] text-xs shrink-0 mt-0.5">→</span>
						<p class="text-zinc-300 text-sm leading-snug">
							<strong class="text-zinc-100">Click a country</strong> on the map
							for a national breakdown, or
							<strong class="text-zinc-100">hover a cell</strong> for local detail
						</p>
					</div>
					<div class="flex gap-2 items-start">
						<span class="text-[#28a889] text-xs shrink-0 mt-0.5">→</span>
						<p class="text-zinc-300 text-sm leading-snug">
							Use the <strong class="text-zinc-100">settings</strong> button to
							change the date or the heat threshold
						</p>
					</div>
				</div>
			{/snippet}
			{@render card(c_howto, "")}

			<!-- Limitations / disclaimer -->
			{#snippet c_limitations()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					Known limitations
				</div>
				<div class="flex flex-col gap-2 flex-1 text-sm text-zinc-300 leading-relaxed">
					<p>
						<strong class="text-zinc-100">Urban heat islands.</strong> The ~6.5 km
						weather model grid cannot fully capture heat buildup within individual
						cities, so we likely underestimate affected people in dense urban areas.
					</p>
					<p>
						<strong class="text-zinc-100">Mountainous regions.</strong> Coarse
						terrain resolution means valley and slope microclimates are not captured,
						which can underestimate exposure in areas like the Alps or Carpathians.
					</p>
					<p>
						<strong class="text-zinc-100">Part forecast.</strong> Today's figures
						blend observed hours with forecast hours, so they can change as new
						model runs arrive.
					</p>
					<p>
						<strong class="text-zinc-100">Population data.</strong> GHS-POP is from
						2020 and does not reflect recent demographic changes.
					</p>
				</div>
			{/snippet}
			{@render card(c_limitations, "")}

			<!-- Feedback -->
			{#snippet c_feedback()}
				<div class="font-bold border-b border-zinc-700 pb-1 mb-3 text-zinc-200">
					Get in touch
				</div>
				<p class="text-zinc-300 text-sm leading-relaxed flex-1">
					Found an issue, have a data suggestion, or want to collaborate? We'd
					love to hear from journalists, researchers and developers.
				</p>
				<a
					href="mailto:team@klimadashboard.org"
					class="mt-4 inline-flex items-center justify-center gap-1.5 px-4 py-2.5 bg-[#1a6d51] hover:bg-[#1d7a5e] text-white font-semibold rounded-lg text-sm transition-colors no-underline"
				>
					team@klimadashboard.org
				</a>
			{/snippet}
			{@render card(c_feedback, "")}
		</div>

		<!-- Full methodology -->
		<details id="methodology" class="mt-6 group">
			<summary
				class="cursor-pointer list-none flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors select-none py-2"
			>
				<svg
					width="12"
					height="12"
					viewBox="0 0 12 12"
					fill="none"
					stroke="currentColor"
					stroke-width="1.8"
					stroke-linecap="round"
					class="transition-transform group-open:rotate-90 shrink-0"
				>
					<path d="M3 2l6 4-6 4" />
				</svg>
				Full methodology
			</summary>

			<div class="mt-4 max-w-2xl text-sm text-zinc-300 leading-relaxed space-y-6">
				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">Population data</h2>
					<p>
						Population figures come from <strong class="text-zinc-100">GHS-POP R2023A</strong>,
						published by the European Commission's Joint Research Centre. This is a global
						population grid at ~1 km² (30 arc-second) resolution, based on the 2020 population
						epoch. We aggregate the raw cells onto a 0.0625° latitude/longitude grid (roughly
						5 x 7 km at European latitudes), resulting in approximately 175,000 grid cells.
					</p>
				</section>

				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">Weather data and model</h2>
					<p class="mb-2">
						Weather data comes from <strong class="text-zinc-100">DWD ICON-EU</strong>,
						Germany's operational weather prediction model at ~6.5 km resolution, published as
						open data in GRIB2 format. DWD publishes 8 model runs per day (00Z, 03Z, 06Z, 09Z,
						12Z, 15Z, 18Z, 21Z). Each run provides hour-by-hour forecasts; our pipeline ingests
						the nearest available run every hour to give continuous hourly coverage across the
						day. Because each run is published roughly 3 hours after its initialisation time,
						the latest analysis always trails the wall clock slightly.
					</p>
					<p>For each grid cell we compute three heat indicators:</p>
					<ul class="mt-2 pl-4 flex flex-col gap-1.5 list-disc text-zinc-300">
						<li><strong class="text-zinc-100">Temperature</strong> — 2m air temperature (°C)</li>
						<li><strong class="text-zinc-100">Feels Like</strong> — Steadman (1994) apparent temperature, accounting for humidity and wind speed</li>
					</ul>
				</section>

				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">Historic climate comparison</h2>
					<p class="mb-2">
						Two of the three headline figures compare today against a baseline from
						<strong class="text-zinc-100">E-OBS</strong> (ECA&amp;D / Copernicus). For each
						grid cell we compute, over the <strong class="text-zinc-100">1961 to 1990</strong>
						reference period and a 15-day window around each calendar date, the mean and the
						90th percentile of daily-mean temperature.
					</p>
					<ul class="pl-4 flex flex-col gap-1.5 list-disc text-zinc-300">
						<li>
							<strong class="text-zinc-100">Difference from average</strong> — today's per-cell
							daily-mean temperature minus the 1961 to 1990 daily-mean, averaged across all grid cells
						</li>
						<li>
							<strong class="text-zinc-100">Uncommonly hot</strong> — people in cells where today's
							daily mean exceeds the 1961 to 1990 90th percentile
						</li>
					</ul>
					<p class="mt-2 text-zinc-400">
						The 1961 to 1990 baseline is the WMO/IPCC reference for climate-change assessment.
						It runs roughly 1°C cooler than the current 1991 to 2020 operational normal, so our
						anomalies read about 1°C warmer than tools on the modern baseline (e.g. Copernicus
						Climate Pulse).
					</p>
				</section>

				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">Heat thresholds</h2>
					<p>
						Default threshold: <strong class="text-zinc-100">30°C</strong> for both temperature
						and feels like. The threshold is adjustable via the settings button and affects
						the "people at X°C or more" headline count, the map cell highlighting, and the
						tooltip range marker.
					</p>
				</section>

				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">How "today" is built</h2>
					<p>
						"Today" covers hourly snapshots across the current UTC day. Early hours are
						recent analysis or short-range forecast; later hours come from the most recent
						model run. The day's figures blend observed-so-far with forecast-for-tonight and
						shift slightly as new runs arrive.
					</p>
					<p class="mt-2">
						The <strong class="text-zinc-100">threshold count</strong> (e.g. 30°C or more) uses
						a daily-maximum view: a cell counts if it reaches the threshold in
						<em>any</em> hourly snapshot. The anomaly and "uncommonly hot" figures use each
						cell's <em>daily mean</em>, the right basis for comparing against the
						daily-mean climatology.
					</p>
				</section>

				<section>
					<h2 class="text-base font-semibold text-zinc-100 mb-2">Data credits</h2>
					<ul class="pl-4 flex flex-col gap-1.5 list-disc text-zinc-300">
						<li>Population: <a href="https://ghsl.jrc.ec.europa.eu/ghs_pop2023.php" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">GHS-POP R2023A</a> — European Commission, JRC</li>
						<li>Weather: <a href="https://opendata.dwd.de/weather/nwp/icon-eu/" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">DWD ICON-EU</a> — Deutscher Wetterdienst open data</li>
						<li>Climatology: <a href="https://www.ecad.eu/download/ensembles/download.php" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">E-OBS</a> — ECA&amp;D / Copernicus</li>
						<li>Basemap: <a href="https://carto.com/basemaps" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">CARTO</a> Dark Matter</li>
						<li>Map rendering: <a href="https://maplibre.org" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">MapLibre GL JS</a></li>
						<li>Source code: <a href="https://github.com/klimadashboard/heat-tracker" target="_blank" rel="noopener" class="text-zinc-200 hover:text-white underline-offset-2 hover:underline">github.com/klimadashboard/heat-tracker</a></li>
					</ul>
				</section>
			</div>
		</details>

		<footer class="mt-6 pt-4 border-t border-zinc-900">
			<p class="text-sm text-zinc-400 text-center">
				European Heat Tracker is an open-source project by
				<a
					class="text-zinc-300 no-underline hover:text-white hover:underline"
					href="https://klimadashboard.org"
					target="_blank"
					rel="noopener">Klimadashboard</a
				>.
				<a
					class="text-zinc-300 no-underline hover:text-white hover:underline"
					href="https://github.com/klimadashboard/heat-tracker"
					target="_blank"
					rel="noopener">GitHub</a
				>
				·
				<a
					class="text-zinc-300 no-underline hover:text-white hover:underline"
					href="https://klimadashboard.org/impressum"
					target="_blank"
					rel="noopener">Imprint</a
				>
			</p>
		</footer>
	</div>
</section>
