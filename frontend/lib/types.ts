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
  analysis_status?: string | null;
  analysis_completed_at?: string | null;
  analysis_model?: string | null;
  analysis_version?: string | null;
};

export type MediaDetail = MediaCard & {
  objects: string[];
  people: string[];
  caption_ai?: string | null;
  caption_dense?: string | null;
  tags_json: string[];
  objects_json: string[];
  landmarks_json: string[];
  scene_json: Record<string, unknown>;
  analysis_attempts: number;
  analysis_error?: string | null;
  metadata_json: Record<string, unknown>;
  segments: SegmentSummary[];
};

export type SearchRequest = {
  query: string;
  media_type?: string | null;
  analysis_status?: string | null;
  trip_name?: string | null;
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
  offset?: number;
  limit?: number;
};

export type SearchResponse = {
  query: string;
  interpreted_filters: Record<string, string | string[] | null>;
  explanation: string;
  total_count: number;
  results: MediaCard[];
};

export type LibraryStats = {
  indexed_media: number;
  indexed_images: number;
  indexed_videos: number;
  mapped_media: number;
  trip_routes: number;
  last_indexed_at?: string | null;
  available_years: LibraryYear[];
};

export type LibraryYear = {
  year: number;
  count: number;
};

export type TimelineMonth = {
  key: string;
  year: number;
  month: number;
  label: string;
  short_label: string;
  count: number;
};

export type YearMediaGroup = {
  year: number;
  count: number;
  items: MediaCard[];
};

export type TripSummary = {
  trip_name: string;
  count: number;
  latest_date?: string | null;
  cover?: MediaCard | null;
};

export type TVHomeResponse = {
  recent_photos: MediaCard[];
  recent_trips: TripSummary[];
  trips_has_more: boolean;
};

export type TVPlaylistResponse = {
  playlist_id: string;
  title: string;
  subtitle?: string | null;
  total_count: number;
  items: MediaCard[];
};

export type ScanStatus = {
  running: boolean;
  status: string;
  pid?: number | null;
  mode?: string | null;
  event?: string | null;
  scanned?: number | null;
  created?: number | null;
  updated?: number | null;
  skipped?: number | null;
  errors?: number | null;
  last_path?: string | null;
  log_updated_at?: string | null;
  detail?: string | null;
};

export type AnalysisStats = {
  pending: number;
  claimed: number;
  completed: number;
  failed: number;
  skipped: number;
  active_workers: number;
  stale_claims: number;
};

export type MapPoint = {
  media_id: string;
  latitude: number;
  longitude: number;
  cluster_size?: number;
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
