<script lang="ts">
	import { get } from "svelte/store";
	import { snapshot, selectedCountry, selectedCountryData, mapInstance, headlineThreshold } from "$lib/stores/data.js";
	import { getCountryName } from "$lib/countries.js";

	let downloading = $state(false);

	function millions(n: number | undefined | null): string {
		if (n == null) return "–";
		if (n >= 1_000_000) {
			const m = n / 1_000_000;
			return `${m >= 10 ? m.toFixed(0) : m.toFixed(1)} million`;
		}
		if (n >= 1_000) return `${(n / 1_000).toFixed(0)},000`;
		return n.toLocaleString("en");
	}

	// Measure text width with wrapping and return lines.
	function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
		const words = text.split(" ");
		const lines: string[] = [];
		let line = "";
		for (const word of words) {
			const test = line ? `${line} ${word}` : word;
			if (ctx.measureText(test).width > maxWidth && line) {
				lines.push(line);
				line = word;
			} else {
				line = test;
			}
		}
		if (line) lines.push(line);
		return lines;
	}

	async function downloadImage() {
		const map = get(mapInstance);
		const snap = get(snapshot);
		const country = get(selectedCountry);
		const countryData = get(selectedCountryData);

		const place = country ? getCountryName(country) : "Europe";
		const anomaly = country
			? (countryData?.avgAnomalyC ?? null)
			: (snap?.meanAnomalyC ?? null);
		const popAboveAvg = country
			? (countryData?.popAboveAvg ?? null)
			: (snap?.popAboveAvg ?? null);
		const affected = country
			? (countryData?.affected ?? null)
			: (snap?.totalAffected ?? null);
		const reference = snap?.referencePeriod ?? "1961–1990";
		const climAvailable = anomaly != null;
		const warmer = anomaly != null && anomaly >= 0;

		downloading = true;
		try {
			// Ensure Barlow is loaded before painting.
			await Promise.allSettled([
				document.fonts.load('700 80px "Barlow"'),
				document.fonts.load('700 30px "Barlow"'),
				document.fonts.load('600 52px "Barlow"'),
				document.fonts.load('400 36px "Barlow"'),
				document.fonts.load('400 30px "Barlow"'),
				document.fonts.load('400 26px "Barlow"'),
			]);

			const W = 1080;
			const H = 1920;
			// Map takes the top 60 %; content fills the rest.
			const MAP_H = Math.round(H * 0.6); // 1152
			const PAD = 64;
			const TEXT_W = W - PAD * 2;

			const canvas = document.createElement("canvas");
			canvas.width = W;
			canvas.height = H;
			const ctx = canvas.getContext("2d")!;

			// ── Background ───────────────────────────────────────────────────────
			ctx.fillStyle = "#0a0a12";
			ctx.fillRect(0, 0, W, H);

			// ── Map ──────────────────────────────────────────────────────────────
			// For the "All of Europe" view, fit to a fixed bounding box first so the
			// portrait crop never clips the continental edges. Then wait for the map
			// idle event (all tiles loaded, all layers fully painted) before reading
			// the canvas — this avoids frames where basemap tiles appear but the
			// grid-circles data layer hasn't rendered yet.
			let mapDataURL: string | null = null;
			if (map) {
				const savedCenter = map.getCenter();
				const savedZoom = map.getZoom();

				if (!country) {
					// Fit the whole continent into the canvas — MapLibre picks the right
					// zoom automatically regardless of the current canvas dimensions.
					// Shifted south (more Med) + lower north (less Scandinavia) + less east
				map.fitBounds([[-10, 30], [40, 60]], { padding: 20, duration: 0, maxZoom: 4 });
				}

				// Phase 1: wait until all sources (incl. GeoJSON) are loaded.
				await new Promise<void>((resolve) => {
					const t = setTimeout(resolve, 6000);
					map.once("idle", () => { clearTimeout(t); resolve(); });
					map.triggerRepaint();
				});

				// Phase 1.5: GeoJSON tiles may still be processing in the worker after
				// idle fires — if so, wait for sourcedata before the final render.
				if (map.getSource('grid-circles') && !map.isSourceLoaded('grid-circles')) {
					await new Promise<void>((resolve) => {
						const t = setTimeout(resolve, 3000);
						const handler = (e: any) => {
							if (e.sourceId === 'grid-circles') {
								map.off('sourcedata', handler);
								clearTimeout(t);
								resolve();
							}
						};
						map.on('sourcedata', handler);
					});
				}

				// Phase 2: one more render frame so the grid-circles paint pass is
				// committed to the canvas before we read it.
				await new Promise<void>((resolve) => {
					const t = setTimeout(resolve, 2000);
					map.once("render", () => { clearTimeout(t); resolve(); });
					map.triggerRepaint();
				});

				try {
					mapDataURL = map.getCanvas().toDataURL("image/png");
				} catch {
					mapDataURL = null;
				}

				if (!country) {
					map.jumpTo({ center: savedCenter, zoom: savedZoom });
				}
			}

			if (mapDataURL) {
				const img = new Image();
				const loaded = await new Promise<boolean>((resolve) => {
					img.onload = () => resolve(true);
					img.onerror = () => resolve(false);
					img.src = mapDataURL!;
				});
				if (loaded) {
					// Cover-scale: fill the map area without distortion, cropping
					// symmetrically on the long axis (map canvas is typically landscape,
					// so we scale to height and center-crop the width).
					const srcW = img.naturalWidth, srcH = img.naturalHeight;
					const scale = Math.max(W / srcW, MAP_H / srcH);
					const scaledW = srcW * scale;
					const scaledH = srcH * scale;
					// Source crop rectangle in original pixel coords
					const sx = (scaledW - W) / 2 / scale;
					const sy = (scaledH - MAP_H) / 2 / scale;
					const sw = W / scale;
					const sh = MAP_H / scale;
					ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, MAP_H);
				} else {
					ctx.fillStyle = "#111128";
					ctx.fillRect(0, 0, W, MAP_H);
				}
			} else {
				ctx.fillStyle = "#111128";
				ctx.fillRect(0, 0, W, MAP_H);
			}

			// Gradient that bleeds the map smoothly into the dark content area.
			const fade = ctx.createLinearGradient(0, MAP_H - 200, 0, MAP_H + 10);
			fade.addColorStop(0, "rgba(10,10,18,0)");
			fade.addColorStop(1, "rgba(10,10,18,1)");
			ctx.fillStyle = fade;
			ctx.fillRect(0, MAP_H - 200, W, 210);

			// ── heat-tracker.eu pill — floats in the gradient/fade zone ──────────
			{
				const urlLabel = "heat-tracker.eu";
				ctx.font = `700 30px "Barlow", system-ui, sans-serif`;
				const urlTextW = ctx.measureText(urlLabel).width;
				const pillPadX = 24;
				const pillH = 48;
				const pillW = urlTextW + pillPadX * 2;
				const pillX = PAD;
				const pillY = MAP_H - 120;
				ctx.fillStyle = "#ffffff";
				ctx.beginPath();
				(ctx as any).roundRect(pillX, pillY, pillW, pillH, pillH / 2);
				ctx.fill();
				ctx.fillStyle = "#0a0a12";
				ctx.fillText(urlLabel, pillX + pillPadX, pillY + pillH * 0.68);
			}

			// ── Content area ─────────────────────────────────────────────────────
			let y = MAP_H + 24;

			// "Today in [Place]" — starts close to the map, number sits further below
			ctx.font = `600 52px "Barlow", system-ui, sans-serif`;
			ctx.fillStyle = "#a1a1aa";
			ctx.fillText(`Today in ${place}`, PAD, y);
			y += 100;

			// Helper: draw one headline block (big number + label line).
			function headline(
				value: string,
				label: string,
				color: string,
				gapAfter = 72,
			) {
				// Big value
				ctx.font = `700 80px "Barlow", system-ui, sans-serif`;
				ctx.fillStyle = color;
				ctx.fillText(value, PAD, y);
				y += 50;

				// Label (wrapped if needed)
				ctx.font = `400 36px "Barlow", system-ui, sans-serif`;
				ctx.fillStyle = "#d4d4d8";
				const lines = wrapText(ctx, label, TEXT_W);
				for (const line of lines) {
					ctx.fillText(line, PAD, y);
					y += 46;
				}
				y += gapAfter;
			}

			headline(
				affected != null ? `~${millions(affected)}` : "–",
				`people experiencing ${get(headlineThreshold)}°C or more`,
				"#f87171", // red-400
			);

			if (climAvailable) {
				headline(
					popAboveAvg != null ? `~${millions(popAboveAvg)}` : "–",
					"people in uncommonly hot conditions",
					"#fbbf24", // amber-400
				);

				const sign = anomaly! >= 0 ? "+" : "";
				headline(
					`${sign}${anomaly!.toFixed(1)}°C`,
					`${warmer ? "warmer" : "cooler"} than the ${reference} average`,
					warmer ? "#fb923c" : "#38bdf8", // orange-400 : sky-400
					0,
				);
			}

			// ── Description ─────────────────────────────────────────────────────
			const dateStr = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
			y += 40;
			ctx.font = `400 30px "Barlow", system-ui, sans-serif`;
			ctx.fillStyle = "#71717a";
			ctx.fillText(`More frequent heatwaves affect millions across ${place}.`, PAD, y);
			y += 42;
			ctx.fillText(`DWD ICON-EU weather model · ${dateStr}`, PAD, y);

			// ── Bottom branding: logo + Klimadashboard.org ───────────────────────
			const logoImg = new Image();
			const logoLoaded = await new Promise<boolean>((resolve) => {
				logoImg.onload = () => resolve(true);
				logoImg.onerror = () => resolve(false);
				logoImg.src = "/logo-klimadashboard.svg";
			});

			const brandY = H - 80;
			const logoSize = 44;
			const textMidY = brandY + logoSize * 0.68;

			if (logoLoaded) {
				ctx.save();
				ctx.beginPath();
				(ctx as any).roundRect(PAD, brandY, logoSize, logoSize, 8);
				ctx.clip();
				ctx.drawImage(logoImg, PAD, brandY, logoSize, logoSize);
				ctx.restore();
			}

			ctx.font = `600 26px "Barlow", system-ui, sans-serif`;
			ctx.fillStyle = "#28a889"; // Klimadashboard teal-green
			ctx.fillText("Klimadashboard.org", PAD + (logoLoaded ? logoSize + 14 : 0), textMidY);

			// ── Download ─────────────────────────────────────────────────────────
			const filename = `heat-tracker-${place.toLowerCase().replace(/\s+/g, "-")}.png`;
			canvas.toBlob(
				(blob) => {
					if (!blob) return;
					const url = URL.createObjectURL(blob);
					const a = document.createElement("a");
					a.href = url;
					a.download = filename;
					document.body.appendChild(a);
					a.click();
					document.body.removeChild(a);
					setTimeout(() => URL.revokeObjectURL(url), 1000);
				},
				"image/png",
			);
		} finally {
			downloading = false;
		}
	}
</script>

<button
	type="button"
	onclick={downloadImage}
	disabled={downloading}
	class="flex items-center gap-2 h-8 px-2.5 bg-zinc-950 border border-zinc-700 hover:border-zinc-500 rounded-lg text-xs text-zinc-400 hover:text-zinc-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
	title="Download Instagram-story image"
>
	<svg
		width="16"
		height="16"
		viewBox="0 0 16 16"
		fill="none"
		stroke="currentColor"
		stroke-width="1.5"
		stroke-linecap="round"
		stroke-linejoin="round"
		class="shrink-0"
	>
		<path d="M8 2v8M5 7l3 3 3-3" />
		<path d="M2 12v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1" />
	</svg>
	{downloading ? "Generating…" : "Download image"}
</button>
