"use client";

import { mediaStreamUrl } from "../lib/api";
import type { MediaDetail } from "../lib/types";

type InspectorDrawerProps = {
  item: MediaDetail | null;
};

function analysisLabel(status?: string | null): { label: string; tone: string } | null {
  switch (status) {
    case "completed":
      return { label: "AI metadata ready", tone: "ready" };
    case "claimed":
      return { label: "AI worker running", tone: "running" };
    case "failed":
      return { label: "AI retry needed", tone: "warning" };
    case "pending":
      return { label: "AI queued", tone: "idle" };
    case "skipped":
      return { label: "Metadata only", tone: "muted" };
    default:
      return null;
  }
}

function formatDate(dateTaken?: string | null): string {
  if (!dateTaken) return "Unknown date";
  return new Date(dateTaken).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatList(values: string[]): string {
  return values.join(", ");
}

export function InspectorDrawer({ item }: InspectorDrawerProps) {
  if (!item) {
    return (
      <aside className="inspectorPanel detailScreen">
        <div className="detailEmptyState">
          <h3>Open a photo</h3>
          <p>Double-click a photo from the stream to open a larger detail view with its date, place, trip, and AI metadata.</p>
        </div>
      </aside>
    );
  }

  const analysis = item.media_type === "image" ? analysisLabel(item.analysis_status) : null;
  const placeHint =
    item.scene_json && typeof item.scene_json["place_hint"] === "string"
      ? item.scene_json["place_hint"]
      : null;

  return (
    <aside className="inspectorPanel detailScreen">
      <div className="detailMediaStage">
        {item.media_type === "video" ? (
          <video className="viewerMedia detailHeroMedia" controls poster={item.thumbnail_url ?? undefined} preload="metadata">
            <source src={mediaStreamUrl(item.id)} />
          </video>
        ) : (
          <img
            alt={item.caption}
            className="viewerMedia detailHeroMedia"
            onError={(event) => {
              const target = event.currentTarget;
              target.style.display = "none";
            }}
            src={mediaStreamUrl(item.id)}
          />
        )}
      </div>

      <div className="detailInfoRail">
        <div className="detailHeaderCard">
          <div className="inspectorHeader">
            <div>
              <p className="eyebrow">Photo details</p>
              <h3>{item.filename}</h3>
            </div>
            <span className={`badge ${item.media_type}`}>{item.media_type}</span>
          </div>
          <p className="inspectorCaption">{item.caption}</p>
        </div>

        <div className="detailMetaGrid">
          <div className="detailMetaCard">
            <p className="fieldLabel">Date</p>
            <p>{formatDate(item.date_taken)}</p>
          </div>
          <div className="detailMetaCard">
            <p className="fieldLabel">Location</p>
            <p>{[item.place, item.city, item.country].filter(Boolean).join(", ") || "Unknown"}</p>
          </div>
          <div className="detailMetaCard">
            <p className="fieldLabel">Trip</p>
            <p>{item.trip_name || "Standalone memory"}</p>
          </div>
          {analysis ? (
            <div className="detailMetaCard">
              <p className="fieldLabel">AI status</p>
              <p><span className={`analysisBadge ${analysis.tone}`}>{analysis.label}</span></p>
            </div>
          ) : null}
        </div>

        <dl className="detailList detailListDense">
          <div>
            <dt>Path</dt>
            <dd className="pathValue">{item.source_path}</dd>
          </div>
          {item.analysis_model ? (
            <div>
              <dt>AI model</dt>
              <dd>{[item.analysis_model, item.analysis_version].filter(Boolean).join(" · ")}</dd>
            </div>
          ) : null}
        </dl>

        {item.explanation ? (
          <div className="inspectorBlock">
            <h4>Why it matched</h4>
            <p>{item.explanation.caption_match || item.caption}</p>
            <p>{item.explanation.transcript_match || item.explanation.location_match || "Matched via semantic similarity."}</p>
          </div>
        ) : null}

        {item.transcript || item.ocr_text ? (
          <div className="inspectorBlock">
            <h4>Text signals</h4>
            {item.transcript ? <p><strong>Transcript:</strong> {item.transcript}</p> : null}
            {item.ocr_text ? <p><strong>OCR:</strong> {item.ocr_text}</p> : null}
          </div>
        ) : null}

        {item.media_type === "image" ? (
          <div className="inspectorBlock">
            <h4>AI image metadata</h4>
            <p>{item.caption_dense || item.caption_ai || "Detailed AI caption is not available yet."}</p>
            {item.tags_json.length ? <p><strong>AI tags:</strong> {formatList(item.tags_json)}</p> : null}
            {item.objects_json.length ? <p><strong>Objects:</strong> {formatList(item.objects_json)}</p> : null}
            {item.landmarks_json.length ? <p><strong>Landmarks:</strong> {formatList(item.landmarks_json)}</p> : null}
            {placeHint ? <p><strong>Place hint:</strong> {placeHint}</p> : null}
            {item.analysis_error ? <p><strong>Worker note:</strong> {item.analysis_error}</p> : null}
          </div>
        ) : null}

        {item.segments.length ? (
          <div className="inspectorBlock">
            <h4>Indexed segments</h4>
            <div className="segmentList">
              {item.segments.map((segment) => (
                <div className="segmentCard" key={segment.id}>
                  <div className="metaRow">
                    <span>{segment.segment_type}</span>
                    <span>
                      {segment.timestamp_start !== null && segment.timestamp_start !== undefined
                        ? `${segment.timestamp_start.toFixed(1)}s`
                        : "n/a"}
                    </span>
                  </div>
                  <p>{segment.caption || segment.content_text}</p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
