"use client";

import type { MediaCard } from "../lib/types";

type ResultGridProps = {
  results: MediaCard[];
  selectedId: string | null;
  onSelect: (mediaId: string) => void;
};

function formatDate(dateTaken?: string | null): string {
  if (!dateTaken) return "Unknown date";
  return new Date(dateTaken).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function ResultGrid({ results, selectedId, onSelect }: ResultGridProps) {
  if (!results.length) {
    return (
      <div className="emptyState">
        <h3>No results</h3>
        <p>Try a broader search, a different tag, or clear the current map bounds.</p>
      </div>
    );
  }

  return (
    <div className="resultGrid">
      {results.map((result) => {
        const active = result.id === selectedId;
        return (
          <button
            className={`mediaCard ${active ? "active" : ""}`}
            key={result.id}
            onClick={() => onSelect(result.id)}
            type="button"
          >
            {result.thumbnail_url ? (
              <img
                alt={result.caption}
                className="mediaThumb"
                onError={(event) => {
                  event.currentTarget.style.display = "none";
                }}
                src={result.thumbnail_url}
              />
            ) : (
              <div className="mediaThumb placeholderThumb">{result.media_type}</div>
            )}
            <div className="mediaMeta">
              <div className="mediaHeadline">
                <span>{result.filename}</span>
                <span className={`badge ${result.media_type}`}>{result.media_type}</span>
              </div>
              <p>{result.caption}</p>
              <div className="metaRow">
                <span>{formatDate(result.date_taken)}</span>
                <span>{[result.place, result.city, result.country].filter(Boolean).join(", ")}</span>
              </div>
              <div className="tagRow">
                {result.tags.slice(0, 4).map((tag) => (
                  <span className="pill" key={`${result.id}-${tag}`}>
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

