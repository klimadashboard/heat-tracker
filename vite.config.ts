import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

// Set API_PROXY in .env.local to forward all /api requests to a remote server
// during local development, e.g.:  API_PROXY=https://heat-tracker.eu
const apiProxy = process.env.API_PROXY;

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		fs: {
			allow: ['data']
		},
		...(apiProxy && {
			proxy: {
				'/api': {
					target: apiProxy,
					changeOrigin: true,
				}
			}
		})
	}
});
