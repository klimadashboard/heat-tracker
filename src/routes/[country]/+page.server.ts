import { error } from '@sveltejs/kit';
import { getCountryCode, getCountryName, getAllSlugs } from '$lib/countries.js';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ params }) => {
	const code = getCountryCode(params.country);

	if (!code) {
		throw error(404, `Country "${params.country}" not found`);
	}

	return {
		countryCode: code,
		countryName: getCountryName(code),
		slug: params.country
	};
};
