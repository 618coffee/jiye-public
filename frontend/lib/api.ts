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

export interface InboxItem {
  note_id: string;
  url: string;
  title: string | null;
  cover_url: string | null;
  author: string | null;
  saved_at: string | null;
  discovered_at: string;
  status: 'pending' | 'processing' | 'promoted' | 'discarded' | 'failed';
  cluster_id: string | null;
  raw_meta: Record<string, unknown>;
  last_error: string | null;
}

export interface InboxCluster {
  id: string;
  name: string;
  description: string | null;
  count: number;
  created_at: string;
  // Time aggregates over member notes (filled by cluster_inbox; may be null
  // for old clusters or when the underlying inbox rows have no time data).
  earliest_discovered_at?: string | null;
  latest_discovered_at?: string | null;
  earliest_saved_at?: string | null;
  latest_saved_at?: string | null;
  notes_added_last_7d?: number | null;
}

export interface InboxStats {
  by_status: Record<string, number>;
  clusters: InboxCluster[];
}

export interface InboxClusterRunResult {
  clusters: InboxCluster[];
  assigned: number;
  skipped: number;
  duration_s: number;
  low_confidence?: number;
  em_moves?: number;
}

export interface InboxPromoteOutcome {
  note_id: string;
  status: 'promoted' | 'failed' | 'skipped';
  error?: string | null;
  title?: string | null;
}

export interface InboxPromoteResult {
  results: InboxPromoteOutcome[];
  promoted: number;
  failed: number;
  skipped: number;
  duration_s: number;
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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
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
  inboxStats: () => get<InboxStats>('/api/inbox/stats'),
  inboxList: (params: { status?: string; cluster_id?: string; title_query?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params.status) qs.set('status', params.status);
    if (params.cluster_id) qs.set('cluster_id', params.cluster_id);
    if (params.title_query) qs.set('title_query', params.title_query);
    if (params.limit !== undefined) qs.set('limit', String(params.limit));
    if (params.offset !== undefined) qs.set('offset', String(params.offset));
    return get<{ items: InboxItem[]; limit: number; offset: number }>(`/api/inbox?${qs.toString()}`);
  },
  inboxRecluster: (body: { target_clusters?: number; sample_size?: number; em_iterations?: number; min_similarity?: number }) =>
    postJson<InboxClusterRunResult>('/api/inbox/cluster', body),
  inboxDiscard: (note_ids: string[]) =>
    postJson<{ discarded: number }>('/api/inbox/discard', { note_ids }),
  inboxPromote: (note_ids: string[], max_per_request?: number) =>
    postJson<InboxPromoteResult>('/api/inbox/promote',
      max_per_request !== undefined
        ? { note_ids, max_per_request }
        : { note_ids }),
  inboxScan: (body?: { url?: string; idle?: number; pause?: number; max_scrolls?: number }) =>
    postJson<{
      scanned: number;
      inserted: number;
      updated: number;
      page_url: string;
      by_status: Record<string, number>;
    }>('/api/inbox/scan', body ?? {}),
};
