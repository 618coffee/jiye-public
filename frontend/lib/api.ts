// Shared API types — mirror backend models.

export interface POI {
  id: number;
  canonical_name: string;
  aliases: string[];
  city: string;
  country: string;
  poi_type: string;
  address?: string | null;
  lat?: number | null;
  lng?: number | null;
  freshness_status?: string | null;
  freshness_score?: number | null;
  last_checked_at?: string | null;
}

export interface NotePOILink {
  poi: POI;
  must_try?: string | null;
  price_per_person?: number | null;
  highlight?: string | null;
}

export interface Note {
  id: string;
  url?: string | null;
  title: string;
  body: string;
  author?: string | null;
  cover?: string | null;
  images: string[];
  saved_at?: string | null;
  published_at?: string | null;
  primary_city?: string | null;
  primary_country?: string | null;
  trip_purpose: string[];
  ai_summary?: string | null;
  image_index_level: number;
  image_thumbnails?: (string | null)[];
  image_ocr_texts?: string[];
  pois: NotePOILink[];
}

export interface CitySummary {
  city: string;
  country: string;
  flag: string;
  note_count: number;
  poi_count: number;
  unverified: number;
  last_refreshed_at?: string | null;
}

export interface CategoryGroup {
  key: string;
  label: string;
  icon: string;
  count: number;
}

export interface WorkbenchData {
  city: string;
  country: string;
  flag: string;
  notes: Note[];
  pois: POI[];
  categories: CategoryGroup[];
  clusters?: WorkbenchClusters;
}

export interface ClusterItem {
  label: string;
  reason: string;
  note_ids: string[];
}

export interface WorkbenchClusters {
  hot: ClusterItem[];
  cold: ClusterItem[];
  dish_radar: ClusterItem[];
  blind_spots: ClusterItem[];
}

export interface SearchHit {
  note: Note;
  score: number;
  matched_chunks: string[];
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  explanation?: string | null;
}

export interface FreshnessRefreshResponse {
  city: string;
  refreshed: number;
  skipped: number;
  pois: POI[];
}

// Server-side (RSC) needs absolute URL pointing at the backend.
// Client-side uses same-origin via next.config rewrites.
const BASE = typeof window === 'undefined'
  ? (process.env.BACKEND_URL || 'http://localhost:8000')
  : '';

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${path}`);
  return r.json();
}

async function post<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: 'POST', cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${path}`);
  return r.json();
}

export const api = {
  cities: () => get<{ cities: CitySummary[] }>('/api/cities'),
  workbench: (city: string) => get<WorkbenchData>(`/api/cities/${encodeURIComponent(city)}/workbench`),
  refresh: (city: string) => post<FreshnessRefreshResponse>(`/api/cities/${encodeURIComponent(city)}/refresh`),
  note: (id: string) => get<Note>(`/api/notes/${encodeURIComponent(id)}`),
  search: (q: string, city?: string) => {
    const params = new URLSearchParams({ q });
    if (city) params.set('city', city);
    return get<SearchResponse>(`/api/search?${params.toString()}`);
  },
  settings: () => get<{ providers: Record<string, string>; data_dir: string; use_mock: string }>('/api/settings'),
};
