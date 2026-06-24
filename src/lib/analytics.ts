// Thin wrapper around the Rybbit analytics API with SSR guard.
// The script is loaded in app.html and exposes window.rybbit.

declare global {
	interface Window {
		rybbit?: {
			pageview: () => void;
			event: (eventName: string, properties?: Record<string, unknown>) => void;
		};
	}
}

export function trackEvent(
	name: string,
	properties?: Record<string, unknown>,
): void {
	if (typeof window === "undefined") return;
	window.rybbit?.event(name, properties);
}
