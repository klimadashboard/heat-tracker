import { existsSync, readFileSync, statSync } from 'fs';
import { readFile, stat } from 'fs/promises';

/**
 * In-memory cache for a single static file produced by the fetch pipeline.
 * `scripts/fetch-dwd.py` rewrites these atomically (os.replace) every ~3h.
 *
 * The cache NEVER blocks a request on disk I/O once primed: `get*()` returns
 * whatever is currently in memory and, if the file's mtime changed, kicks off
 * an asynchronous reload in the background. The previous (a-few-ms-stale) copy
 * keeps being served until that reload finishes.
 *
 * This is the fix for "the first page load right after a fetch run takes ~a
 * minute, then it's fast". Previously the first request after a regeneration
 * had to synchronously re-read the file (cache invalidated by the new mtime)
 * exactly while the host was still IO-saturated from the fetch — rewriting the
 * 723 MB SQLite DB, the ~400 MB climatology and ~150 MB of GeoJSON — so that
 * one request stalled. Every later request hit the warm cache and was instant.
 * Serving the prior snapshot for a moment is harmless: the data only changes
 * every ~3h anyway.
 */
export class CachedFile {
	private data: Buffer | null = null;
	private mtimeMs = -1;
	private refreshing = false;

	constructor(private readonly path: string) {
		// Prime synchronously at construction so the first request is already
		// served from memory. Modules instantiate the hot files at boot (see the
		// API routes), making this a one-time startup cost rather than a per-fetch
		// stall. Large raw fallbacks are created lazily, so this stays cheap.
		try {
			if (existsSync(this.path)) {
				const st = statSync(this.path);
				this.data = readFileSync(this.path);
				this.mtimeMs = st.mtimeMs;
			}
		} catch {
			// Leave empty; the first get*() will populate it in the background.
		}
	}

	/**
	 * Current cached bytes (or null if the file has never been readable).
	 * Triggers a non-blocking background refresh when the file changed on disk.
	 */
	getBuffer(): Buffer | null {
		void this.maybeRefresh();
		return this.data;
	}

	getText(): string | null {
		const buf = this.getBuffer();
		return buf ? buf.toString('utf-8') : null;
	}

	private async maybeRefresh(): Promise<void> {
		if (this.refreshing) return;
		this.refreshing = true;
		try {
			const st = await stat(this.path);
			if (st.mtimeMs !== this.mtimeMs) {
				const data = await readFile(this.path);
				this.data = data;
				this.mtimeMs = st.mtimeMs;
			}
		} catch {
			// File missing or mid-write — keep the last good copy.
		} finally {
			this.refreshing = false;
		}
	}
}

const registry = new Map<string, CachedFile>();

/** Get (creating + priming on first use) the shared cache for a file path. */
export function cachedFile(path: string): CachedFile {
	let cf = registry.get(path);
	if (!cf) {
		cf = new CachedFile(path);
		registry.set(path, cf);
	}
	return cf;
}
