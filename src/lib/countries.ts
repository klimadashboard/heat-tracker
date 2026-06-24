/** Canonical country data shared across components and routes */

export const COUNTRY_NAMES: Record<string, string> = {
	AL: 'Albania',
	AD: 'Andorra',
	AT: 'Austria',
	BY: 'Belarus',
	BE: 'Belgium',
	BA: 'Bosnia & Herzegovina',
	BG: 'Bulgaria',
	HR: 'Croatia',
	CY: 'Cyprus',
	CZ: 'Czechia',
	DK: 'Denmark',
	EE: 'Estonia',
	FI: 'Finland',
	FR: 'France',
	DE: 'Germany',
	GR: 'Greece',
	HU: 'Hungary',
	IS: 'Iceland',
	IE: 'Ireland',
	IT: 'Italy',
	XK: 'Kosovo',
	LV: 'Latvia',
	LT: 'Lithuania',
	LU: 'Luxembourg',
	MK: 'North Macedonia',
	MT: 'Malta',
	MD: 'Moldova',
	ME: 'Montenegro',
	NL: 'Netherlands',
	NO: 'Norway',
	PL: 'Poland',
	PT: 'Portugal',
	RO: 'Romania',
	RS: 'Serbia',
	SK: 'Slovakia',
	SI: 'Slovenia',
	ES: 'Spain',
	SE: 'Sweden',
	CH: 'Switzerland',
	UA: 'Ukraine',
	GB: 'United Kingdom'
};

/** URL slug → country code */
export const SLUG_TO_CODE: Record<string, string> = {};
/** Country code → URL slug */
export const CODE_TO_SLUG: Record<string, string> = {};

for (const [code, name] of Object.entries(COUNTRY_NAMES)) {
	const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+$/, '');
	SLUG_TO_CODE[slug] = code;
	CODE_TO_SLUG[code] = slug;
}

export function getCountryName(code: string): string {
	return COUNTRY_NAMES[code] || code;
}

export function getCountryCode(slug: string): string | null {
	return SLUG_TO_CODE[slug] || null;
}

export function getAllSlugs(): string[] {
	return Object.keys(SLUG_TO_CODE);
}
