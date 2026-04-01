import type { AnalysisStats, LibraryStats, LibraryYear, MapResponse, MediaCard, MediaDetail, ScanStatus, SearchRequest, SearchResponse, TimelineMonth, TripSummary, TVHomeResponse, TVPlaylistResponse, YearMediaGroup } from "./types";

const MONTHS_CACHE_TTL_MS = 60_000;

function resolveApiBase(): string {
  const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configuredBase) {
    return configuredBase.replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    return `${window.location.origin}/api`;
  }
  return "http://localhost:8000/api";
}

export function mediaStreamUrl(mediaId: string): string {
  return `${resolveApiBase()}/media/${mediaId}/stream`;
}

export function mediaPreviewUrl(mediaId: string, variant: "default" | "tv" = "default"): string {
  const suffix = variant === "tv" ? "/preview/tv" : "/preview";
  return `${resolveApiBase()}/media/${mediaId}${suffix}`;
}

export async function fetchTVHome(params?: {
  recentLimit?: number;
  tripLimit?: number;
  tripOffset?: number;
  analysisStatus?: string;
}, signal?: AbortSignal): Promise<TVHomeResponse> {
  const query = new URLSearchParams();
  if (params?.recentLimit) query.set("recent_limit", String(params.recentLimit));
  if (params?.tripLimit) query.set("trip_limit", String(params.tripLimit));
  if (params?.tripOffset) query.set("trip_offset", String(params.tripOffset));
  if (params?.analysisStatus) query.set("analysis_status", params.analysisStatus);
  const response = await fetch(`${resolveApiBase()}/tv/home?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`TV home request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchTVPlaylist(params: {
  kind: "recent" | "trip";
  tripName?: string;
  limit?: number;
  offset?: number;
  analysisStatus?: string;
}, signal?: AbortSignal): Promise<TVPlaylistResponse> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  const endpoint = params.kind === "trip" && params.tripName
    ? `${resolveApiBase()}/tv/playlists/trips/${encodeURIComponent(params.tripName)}`
    : `${resolveApiBase()}/tv/playlists/recent`;
  const response = await fetch(`${endpoint}?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`TV playlist request failed with ${response.status}`);
  }
  return response.json();
}

export async function searchMedia(request: SearchRequest, signal?: AbortSignal): Promise<SearchResponse> {
  const response = await fetch(`${resolveApiBase()}/search/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Search request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchLibraryStats(signal?: AbortSignal): Promise<LibraryStats> {
  const response = await fetch(`${resolveApiBase()}/library/stats`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Library stats request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchRecentMedia(params: {
  limit?: number;
  offset?: number;
  mediaType?: string;
  analysisStatus?: string;
  tripName?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
  bbox?: string;
}, signal?: AbortSignal): Promise<MediaCard[]> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.tripName) query.set("trip_name", params.tripName);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);
  if (params.bbox) query.set("bbox", params.bbox);

  const response = await fetch(`${resolveApiBase()}/media?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Recent media request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchYearMediaGroups(params: {
  perYear?: number;
  mediaType?: string;
  analysisStatus?: string;
  tripName?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}, signal?: AbortSignal): Promise<YearMediaGroup[]> {
  const query = new URLSearchParams();
  if (params.perYear) query.set("per_year", String(params.perYear));
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.tripName) query.set("trip_name", params.tripName);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const response = await fetch(`${resolveApiBase()}/media/year-groups?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Year media groups request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMediaYears(params: {
  mediaType?: string;
  analysisStatus?: string;
  tripName?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}, signal?: AbortSignal): Promise<LibraryYear[]> {
  const query = new URLSearchParams();
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.tripName) query.set("trip_name", params.tripName);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const response = await fetch(`${resolveApiBase()}/media/years?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Media years request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMediaMonths(params: {
  year?: number;
  mediaType?: string;
  analysisStatus?: string;
  tripName?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}, signal?: AbortSignal): Promise<TimelineMonth[]> {
  const query = new URLSearchParams();
  if (params.year != null) query.set("year", String(params.year));
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.tripName) query.set("trip_name", params.tripName);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const queryString = query.toString();
  const cacheKey = `photohunting:months:${queryString}`;
  if (typeof window !== "undefined") {
    try {
      const cached = window.sessionStorage.getItem(cacheKey);
      if (cached) {
        const payload = JSON.parse(cached) as { savedAt?: number; data?: TimelineMonth[] };
        if (
          payload.savedAt
          && Array.isArray(payload.data)
          && Date.now() - payload.savedAt < MONTHS_CACHE_TTL_MS
        ) {
          return payload.data;
        }
      }
    } catch {
      // Ignore sessionStorage read issues and fall back to the network.
    }
  }

  const response = await fetch(`${resolveApiBase()}/media/months?${queryString}`, {
    cache: "default",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Media months request failed with ${response.status}`);
  }
  const months = await response.json();
  if (typeof window !== "undefined") {
    try {
      window.sessionStorage.setItem(cacheKey, JSON.stringify({
        savedAt: Date.now(),
        data: months,
      }));
    } catch {
      // Ignore sessionStorage write issues.
    }
  }
  return months;
}

export async function fetchTripSummaries(params: {
  limit?: number;
  mediaType?: string;
  analysisStatus?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}, signal?: AbortSignal): Promise<TripSummary[]> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const response = await fetch(`${resolveApiBase()}/media/trips?${query.toString()}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Trip summaries request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchScanStatus(signal?: AbortSignal): Promise<ScanStatus> {
  const response = await fetch(`${resolveApiBase()}/library/scan-status`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Scan status request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchAnalysisStats(signal?: AbortSignal): Promise<AnalysisStats> {
  const response = await fetch(`${resolveApiBase()}/analysis/stats`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`Analysis stats request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMapPoints(params: {
  bbox?: string;
  zoom?: number;
  mediaType?: string;
  analysisStatus?: string;
  tripName?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}, signal?: AbortSignal): Promise<MapResponse> {
  const query = new URLSearchParams();
  if (params.bbox) query.set("bbox", params.bbox);
  if (params.zoom) query.set("zoom", String(params.zoom));
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.analysisStatus) query.set("analysis_status", params.analysisStatus);
  if (params.tripName) query.set("trip_name", params.tripName);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const response = await fetch(`${resolveApiBase()}/map/points?${query.toString()}`, { cache: "no-store", signal });
  if (!response.ok) {
    throw new Error(`Map request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMedia(mediaId: string, signal?: AbortSignal): Promise<MediaDetail> {
  const response = await fetch(`${resolveApiBase()}/media/${mediaId}`, { cache: "no-store", signal });
  if (!response.ok) {
    throw new Error(`Media request failed with ${response.status}`);
  }
  return response.json();
}
