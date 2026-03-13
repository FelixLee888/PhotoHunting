export type Explanation = {
  caption_match?: string | null;
  transcript_match?: string | null;
  location_match?: string | null;
  visual_similarity?: number | null;
  matched_tags: string[];
};

export type SegmentSummary = {
  id: string;
  segment_type: string;
  timestamp_start?: number | null;
  timestamp_end?: number | null;
  caption?: string | null;
  content_text?: string | null;
  thumbnail_url?: string | null;
};

export type MediaCard = {
  id: string;
  filename: string;
  source_path: string;
  media_type: "image" | "video" | string;
  caption: string;
  tags: string[];
  country?: string | null;
  region?: string | null;
  city?: string | null;
  place?: string | null;
  landmark?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  thumbnail_url?: string | null;
  date_taken?: string | null;
  duration?: number | null;
  transcript?: string | null;
  ocr_text?: string | null;
  explanation?: Explanation | null;
  score?: number | null;
  trip_name?: string | null;
};

export type MediaDetail = MediaCard & {
  objects: string[];
  people: string[];
  metadata_json: Record<string, unknown>;
  segments: SegmentSummary[];
};

export type SearchRequest = {
  query: string;
  media_type?: string | null;
  tags?: string[];
  country?: string | null;
  region?: string | null;
  city?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  bbox?: string | null;
  near_latitude?: number | null;
  near_longitude?: number | null;
  near_radius_km?: number | null;
  limit?: number;
};

export type SearchResponse = {
  query: string;
  interpreted_filters: Record<string, string | string[] | null>;
  explanation: string;
  results: MediaCard[];
};

export type MapPoint = {
  media_id: string;
  latitude: number;
  longitude: number;
  thumbnail?: string | null;
  caption?: string | null;
  date?: string | null;
  media_type: string;
  tags: string[];
  location?: string | null;
  trip_name?: string | null;
};

export type MapRoute = {
  trip_name: string;
  media_ids: string[];
  coordinates: [number, number][];
};

export type MapResponse = {
  points: MapPoint[];
  routes: MapRoute[];
};

