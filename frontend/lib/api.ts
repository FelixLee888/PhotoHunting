import type { MapResponse, MediaDetail, SearchRequest, SearchResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

export function mediaStreamUrl(mediaId: string): string {
  return `${API_BASE}/media/${mediaId}/stream`;
}

export async function searchMedia(request: SearchRequest): Promise<SearchResponse> {
  const response = await fetch(`${API_BASE}/search/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Search request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMapPoints(params: {
  bbox?: string;
  mediaType?: string;
  country?: string;
  region?: string;
  city?: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<MapResponse> {
  const query = new URLSearchParams();
  if (params.bbox) query.set("bbox", params.bbox);
  if (params.mediaType) query.set("media_type", params.mediaType);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.city) query.set("city", params.city);
  if (params.tag) query.set("tag", params.tag);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);

  const response = await fetch(`${API_BASE}/map/points?${query.toString()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Map request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchMedia(mediaId: string): Promise<MediaDetail> {
  const response = await fetch(`${API_BASE}/media/${mediaId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Media request failed with ${response.status}`);
  }
  return response.json();
}

