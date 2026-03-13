"use client";

import { mediaStreamUrl } from "../lib/api";
import type { MediaDetail } from "../lib/types";

type InspectorDrawerProps = {
  item: MediaDetail | null;
};

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

export function InspectorDrawer({ item }: InspectorDrawerProps) {
  if (!item) {
    return (
      <aside className="inspectorPanel">
        <h3>Media viewer</h3>
        <p>Select a photo or video to inspect captions, route context, OCR, and transcript matches.</p>
      </aside>
    );
  }

  return (
    <aside className="inspectorPanel">
      <div className="inspectorHeader">
        <div>
          <p className="eyebrow">Media viewer</p>
          <h3>{item.filename}</h3>
        </div>
        <span className={`badge ${item.media_type}`}>{item.media_type}</span>
      </div>

      {item.media_type === "video" ? (
        <video className="viewerMedia" controls poster={item.thumbnail_url ?? undefined} preload="metadata">
          <source src={mediaStreamUrl(item.id)} />
        </video>
      ) : (
        <img
          alt={item.caption}
          className="viewerMedia"
          onError={(event) => {
            const target = event.currentTarget;
            target.style.display = "none";
          }}
          src={item.thumbnail_url || mediaStreamUrl(item.id)}
        />
      )}

      <p className="inspectorCaption">{item.caption}</p>

      <dl className="detailList">
        <div>
          <dt>Date</dt>
          <dd>{formatDate(item.date_taken)}</dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>{[item.place, item.city, item.country].filter(Boolean).join(", ") || "Unknown"}</dd>
        </div>
        <div>
          <dt>Trip</dt>
          <dd>{item.trip_name || "Standalone memory"}</dd>
        </div>
        <div>
          <dt>Path</dt>
          <dd className="pathValue">{item.source_path}</dd>
        </div>
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
    </aside>
  );
}
