<script lang="ts">
	import { onMount } from "svelte";
	import InfoSection from "$lib/components/InfoSection.svelte";

	let { data } = $props();

	onMount(() => {
		document.getElementById("map")?.scrollIntoView({ behavior: "instant" });
	});
</script>

<svelte:head>
	<title
		>{data.countryName} - Extreme Heat Exposure | European Heat Tracker</title
	>
	<meta
		name="description"
		content="Real-time extreme heat exposure data for {data.countryName}. See how many people are affected by dangerous temperatures."
	/>
</svelte:head>

<InfoSection />
