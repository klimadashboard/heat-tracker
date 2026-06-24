export function formatPopulation(n: number): string {
	if (n >= 1_000_000) {
		return `${(n / 1_000_000).toFixed(1)}M`;
	}
	if (n >= 1_000) {
		return `${(n / 1_000).toFixed(0)}K`;
	}
	return n.toString();
}

export function formatPopulationLong(n: number): string {
	if (n >= 1_000_000) {
		const m = n / 1_000_000;
		if (m >= 10) return `${m.toFixed(1)} million`;
		return `${m.toFixed(2)} million`;
	}
	if (n >= 1_000) {
		return `${(n / 1_000).toFixed(0)},000`;
	}
	return n.toLocaleString();
}

export function formatTemperature(t: number | null): string {
	if (t === null) return '–';
	return `${t.toFixed(1)}°C`;
}

export function formatPercent(n: number, total: number): string {
	if (total === 0) return '0%';
	return `${((n / total) * 100).toFixed(1)}%`;
}

export function timeAgo(timestamp: string): string {
	const diff = Date.now() - new Date(timestamp).getTime();
	const minutes = Math.floor(diff / 60000);
	if (minutes < 1) return 'just now';
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	return `${days}d ago`;
}
