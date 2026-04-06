"use client";

import { memo, useMemo } from "react";

import type { MediaCard, YearMediaGroup } from "../lib/types";

type TripStory = {
  tripName: string;
  rawTripName: string;
  count: number;
  cover: MediaCard;
  latestTimestamp: number;
};

type DayGroup = {
  key: string;
  label: string;
  items: MediaCard[];
};

type MonthGroup = {
  key: string;
  label: string;
  items: DayGroup[];
};

type RenderDayGroup = DayGroup & {
  tiles: SemanticTile[];
};

type RenderMonthGroup = {
  key: string;
  label: string;
  items: RenderDayGroup[];
};

type SemanticTile =
  | {
    kind: "media";
    key: string;
    item: MediaCard;
    hero: boolean;
  }
  | {
    kind: "stack";
    key: string;
    cover: MediaCard;
    items: MediaCard[];
    label: string;
  };

type ResultGridProps = {
  emptyDescription?: string;
  emptyTitle?: string;
  results: MediaCard[];
  yearGroups?: YearMediaGroup[];
  selectedId: string | null;
  activeTripName?: string | null;
  tripStoryYear?: number | null;
  density?: "comfortable" | "compact";
  groupByDay?: boolean;
  compactStoryStrip?: boolean;
  onSelect: (mediaId: string) => void;
  onOpen: (mediaId: string) => void;
  onOpenYear?: (year: number) => void;
  onFilterTrip?: (tripName: string) => void;
};

function formatDate(dateTaken?: string | null): string {
  if (!dateTaken) return "Recent";
  return new Date(dateTaken).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function matchesStoryYear(result: MediaCard, year: number | null): boolean {
  if (!year) {
    return true;
  }
  if (!result.date_taken) {
    return false;
  }
  const parsed = new Date(result.date_taken);
  if (Number.isNaN(parsed.getTime())) {
    return false;
  }
  return parsed.getFullYear() === year;
}

function storyTitle(result: MediaCard): string {
  return result.trip_name || result.place || result.city || result.caption || result.filename;
}

function storySubtitle(result: MediaCard): string {
  if (result.date_taken) {
    const deltaYears = new Date().getFullYear() - new Date(result.date_taken).getFullYear();
    if (deltaYears > 0) {
      return `${deltaYears} year${deltaYears === 1 ? "" : "s"} ago`;
    }
  }
  return [result.place, result.city, result.country].filter(Boolean).join(", ") || "From your library";
}

function inferTripDisplayName(result: MediaCard): string | null {
  const path = result.source_path || "";
  if (path) {
    const segments = path.split("/").filter(Boolean).reverse();
    for (const segment of segments) {
      const candidate = segment.trim().replace(/\s+/g, " ");
      if (/^\d{4}-\d{2}-\d{2}\s+\S/.test(candidate)) {
        return candidate;
      }
      if (/^\d{4}-\d{2}\s+\S/.test(candidate)) {
        return candidate;
      }
    }
  }
  return result.trip_name || null;
}

function buildTripStories(results: MediaCard[]): TripStory[] {
  const groups = new Map<string, TripStory>();
  for (const result of results) {
    const displayTripName = inferTripDisplayName(result);
    if (!displayTripName) {
      continue;
    }
    const existing = groups.get(displayTripName);
    if (existing) {
      existing.count += 1;
      const currentDate = existing.cover.date_taken ? new Date(existing.cover.date_taken).getTime() : 0;
      const nextDate = result.date_taken ? new Date(result.date_taken).getTime() : 0;
      existing.latestTimestamp = Math.max(existing.latestTimestamp, nextDate);
      if (currentDate === 0 || (nextDate > 0 && nextDate < currentDate)) {
        existing.cover = result;
      }
      continue;
    }
    const resultTimestamp = result.date_taken ? new Date(result.date_taken).getTime() : 0;
    groups.set(displayTripName, {
      tripName: displayTripName,
      rawTripName: result.trip_name || displayTripName,
      count: 1,
      cover: result,
      latestTimestamp: resultTimestamp,
    });
  }

  return [...groups.values()]
    .sort((left, right) => {
      if (right.count !== left.count) {
        return right.count - left.count;
      }
      return right.latestTimestamp - left.latestTimestamp;
    })
    .slice(0, 6);
}

function buildDayGroups(results: MediaCard[]): DayGroup[] {
  const groups: DayGroup[] = [];
  const groupByKey = new Map<string, DayGroup>();

  for (const result of results) {
    const parsed = result.date_taken ? new Date(result.date_taken) : null;
    const key = parsed && !Number.isNaN(parsed.getTime())
      ? parsed.toISOString().slice(0, 10)
      : "undated";
    const label = parsed && !Number.isNaN(parsed.getTime())
      ? parsed.toLocaleDateString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      })
      : "Undated";
    const existing = groupByKey.get(key);
    if (existing) {
      existing.items.push(result);
      continue;
    }
    const nextGroup = { key, label, items: [result] };
    groupByKey.set(key, nextGroup);
    groups.push(nextGroup);
  }

  return groups;
}

function buildMonthGroups(results: MediaCard[]): MonthGroup[] {
  const groups: MonthGroup[] = [];
  const groupByKey = new Map<string, MonthGroup>();

  for (const dayGroup of buildDayGroups(results)) {
    const dayParsed = dayGroup.key !== "undated" ? new Date(dayGroup.key) : null;
    const monthKey = dayParsed && !Number.isNaN(dayParsed.getTime())
      ? dayGroup.key.slice(0, 7)
      : "undated";
    const label = dayParsed && !Number.isNaN(dayParsed.getTime())
      ? dayParsed.toLocaleDateString(undefined, {
        month: "long",
        year: "numeric",
      })
      : "Undated";

    const existing = groupByKey.get(monthKey);
    if (existing) {
      existing.items.push(dayGroup);
      continue;
    }

    const nextGroup = { key: monthKey, label, items: [dayGroup] };
    groupByKey.set(monthKey, nextGroup);
    groups.push(nextGroup);
  }

  return groups;
}

function isTechnicalShot(result: MediaCard): boolean {
  const sample = [result.filename, result.caption, result.ocr_text, ...(result.tags ?? [])]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return [
    "receipt",
    "screenshot",
    "screen shot",
    "scan",
    "invoice",
    "document",
    "statement",
  ].some((token) => sample.includes(token));
}

function importanceScore(result: MediaCard): number {
  let score = 0;
  if (result.analysis_status === "completed") score += 3;
  if (result.trip_name) score += 3;
  if (result.place || result.city || result.country) score += 2;
  if ((result.caption || "").length > 32) score += 1;
  if (result.media_type === "video") score += 1;
  return score;
}

function buildSemanticTiles(items: MediaCard[]): SemanticTile[] {
  if (!items.length) {
    return [];
  }

  const technicalShots = items.filter(isTechnicalShot);
  const technicalIds = new Set(technicalShots.map((item) => item.id));
  const scenicShots = items.filter((item) => !technicalIds.has(item.id));
  const heroIds = new Set(
    [...scenicShots]
      .sort((left, right) => importanceScore(right) - importanceScore(left))
      .slice(0, Math.min(2, scenicShots.length))
      .map((item) => item.id),
  );

  const tiles: SemanticTile[] = scenicShots.map((item, index) => ({
    kind: "media",
    key: item.id,
    item,
    hero: heroIds.has(item.id) || (index === 0 && scenicShots.length > 3),
  }));

  if (technicalShots.length >= 3) {
    const cover = technicalShots[0];
    const insertIndex = Math.min(tiles.length, 2);
    tiles.splice(insertIndex, 0, {
      kind: "stack",
      key: `stack-${cover.id}`,
      cover,
      items: technicalShots,
      label: "Detail stack",
    });
  } else {
    tiles.push(
      ...technicalShots.map((item) => ({
        kind: "media" as const,
        key: item.id,
        item,
        hero: false,
      })),
    );
  }

  return tiles;
}

function buildFlatTiles(items: MediaCard[]): SemanticTile[] {
  return items.map((item) => ({
    kind: "media" as const,
    key: item.id,
    item,
    hero: false,
  }));
}

export const ResultGrid = memo(function ResultGrid({
  emptyDescription = "Try a broader search, a different tag, or clear the current map bounds.",
  emptyTitle = "No results",
  results,
  yearGroups = [],
  selectedId,
  activeTripName = null,
  tripStoryYear = null,
  density = "comfortable",
  groupByDay = false,
  compactStoryStrip = false,
  onSelect,
  onOpen,
  onOpenYear,
  onFilterTrip,
}: ResultGridProps) {
  if (yearGroups.length) {
    return (
      <div className="googlePhotosFeed yearOverviewFeed">
        {yearGroups.map((group) => (
          <section className="yearSection" id={`year-${group.year}`} key={group.year}>
            <div className="streamDateHeader yearSectionHeader">
              <div>
                <strong>{group.year}</strong>
                <span>{group.count.toLocaleString()} photos</span>
              </div>
              {onOpenYear ? (
                <button className="yearSectionButton" onClick={() => onOpenYear(group.year)} type="button">
                  View year
                </button>
              ) : null}
            </div>
            <div className={`photoTileGrid ${density === "compact" ? "compact" : "comfortable"}`}>
              {group.items.map((result) => {
                const active = result.id === selectedId;
                return (
                  <button
                    className={`photoTile ${active ? "active" : ""}`}
                    onDoubleClick={() => onOpen(result.id)}
                    key={result.id}
                    onClick={() => onSelect(result.id)}
                    type="button"
                  >
                    {result.thumbnail_url ? (
                      <img
                        alt={result.caption}
                        className="photoTileImage"
                        decoding="async"
                        loading="lazy"
                        onError={(event) => {
                          event.currentTarget.style.display = "none";
                        }}
                        src={result.thumbnail_url}
                      />
                    ) : (
                      <div className="photoTileImage placeholderThumb">{result.media_type}</div>
                    )}
                    {result.media_type === "video" ? <span className="photoTileBadge">Video</span> : null}
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    );
  }

  if (!results.length) {
    return (
      <div className="emptyState">
        <h3>{emptyTitle}</h3>
        <p>{emptyDescription}</p>
      </div>
    );
  }

  const tripStories = useMemo(() => {
    const scopedResults = tripStoryYear
      ? results.filter((result) => matchesStoryYear(result, tripStoryYear))
      : results;
    return buildTripStories(scopedResults);
  }, [results, tripStoryYear]);
  const highlightCards = useMemo(
    () => (tripStories.length ? [] : results.slice(0, Math.min(3, results.length))),
    [results, tripStories.length],
  );
  const useSemanticLayout = density === "comfortable" && results.length <= 240;
  const groupedCards = useMemo<RenderMonthGroup[]>(() => {
    const streamCards = highlightCards.length ? results.slice(highlightCards.length) : results;
    const defaultCards = streamCards.length ? streamCards : results;
    const streamHeaderDate = formatDate(defaultCards[0]?.date_taken ?? results[0]?.date_taken ?? null);

    const baseGroups = groupByDay
      ? buildMonthGroups(defaultCards)
      : [{ key: "all", label: streamHeaderDate, items: [{ key: "all", label: streamHeaderDate, items: defaultCards }] }];

    return baseGroups.map((group) => ({
      ...group,
      items: group.items.map((dayGroup) => ({
        ...dayGroup,
        tiles: useSemanticLayout ? buildSemanticTiles(dayGroup.items) : buildFlatTiles(dayGroup.items),
      })),
    }));
  }, [groupByDay, highlightCards.length, results, useSemanticLayout]);

  return (
    <div className="googlePhotosFeed">
      {tripStories.length ? (
        <div className={`storyStrip tripStoryStrip ${compactStoryStrip ? "compactVariant" : ""}`}>
          {tripStories.map((trip) => {
            const active = trip.tripName === activeTripName || trip.rawTripName === activeTripName;
            const result = trip.cover;
            return (
              <button
                className={`storyCard ${compactStoryStrip ? "compactStoryCard" : ""} ${active ? "active tripActive" : ""}`}
                key={trip.tripName}
                onClick={() => onFilterTrip?.(trip.rawTripName)}
                type="button"
              >
                {result.thumbnail_url ? (
                  <img
                    alt={result.caption}
                    className="storyImage"
                    decoding="async"
                    loading="lazy"
                    onError={(event) => {
                      event.currentTarget.style.display = "none";
                    }}
                    src={result.thumbnail_url}
                  />
                ) : (
                  <div className="storyImage placeholderThumb">{result.media_type}</div>
                )}
                <div className="storyOverlay" />
                <div className="storyText">
                  <strong>{trip.tripName}</strong>
                  <span>{trip.count.toLocaleString()} photos</span>
                </div>
              </button>
            );
          })}
        </div>
      ) : highlightCards.length ? (
        <div className="storyStrip">
          {highlightCards.map((result) => {
            const active = result.id === selectedId;
            return (
              <button
                className={`storyCard ${active ? "active" : ""}`}
                onDoubleClick={() => onOpen(result.id)}
                key={result.id}
                onClick={() => onSelect(result.id)}
                type="button"
              >
                {result.thumbnail_url ? (
                  <img
                    alt={result.caption}
                    className="storyImage"
                    decoding="async"
                    loading="lazy"
                    onError={(event) => {
                      event.currentTarget.style.display = "none";
                    }}
                    src={result.thumbnail_url}
                  />
                ) : (
                  <div className="storyImage placeholderThumb">{result.media_type}</div>
                )}
                <div className="storyOverlay" />
                <div className="storyText">
                  <strong>{storyTitle(result)}</strong>
                  <span>{storySubtitle(result)}</span>
                </div>
              </button>
            );
          })}
        </div>
      ) : null}

      {groupedCards.map((group) => (
        <section className="feedMonthSection" id={group.key !== "all" ? `month-${group.key}` : undefined} key={group.key}>
          {group.key !== "all" ? <div className="streamMonthHeader">{group.label}</div> : null}
          {group.items.map((dayGroup) => (
            <section className="feedDaySection" key={dayGroup.key}>
              <div className="streamDateHeader">{dayGroup.label}</div>
              <div className={`photoTileGrid ${density === "compact" ? "compact" : "comfortable"}`}>
                {dayGroup.tiles.map((tile) => {
                  if (tile.kind === "stack") {
                    return (
                      <button
                        className="photoTile stackTile"
                        key={tile.key}
                        onDoubleClick={() => onOpen(tile.cover.id)}
                        onClick={() => onSelect(tile.cover.id)}
                        type="button"
                      >
                        {tile.cover.thumbnail_url ? (
                          <img
                            alt={tile.cover.caption}
                            className="photoTileImage"
                            decoding="async"
                            loading="lazy"
                            onError={(event) => {
                              event.currentTarget.style.display = "none";
                            }}
                            src={tile.cover.thumbnail_url}
                          />
                        ) : (
                          <div className="photoTileImage placeholderThumb">{tile.cover.media_type}</div>
                        )}
                        <span className="photoTileBadge stackCount">{tile.items.length} items</span>
                        <div className="photoTileStackLabel">
                          <strong>{tile.label}</strong>
                          <span>Receipts, screenshots, or similar captures</span>
                        </div>
                      </button>
                    );
                  }

                  const result = tile.item;
                  const active = result.id === selectedId;
                  return (
                    <button
                      className={`photoTile ${active ? "active" : ""} ${tile.hero ? "hero" : ""}`}
                      onDoubleClick={() => onOpen(result.id)}
                      key={tile.key}
                      onClick={() => onSelect(result.id)}
                      type="button"
                    >
                      {result.thumbnail_url ? (
                        <img
                          alt={result.caption}
                          className="photoTileImage"
                          decoding="async"
                          loading="lazy"
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                          src={result.thumbnail_url}
                        />
                      ) : (
                        <div className="photoTileImage placeholderThumb">{result.media_type}</div>
                      )}
                      {result.media_type === "video" ? <span className="photoTileBadge">Video</span> : null}
                      {tile.hero ? <span className="semanticHeroBadge">Hero</span> : null}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </section>
      ))}
    </div>
  );
});

ResultGrid.displayName = "ResultGrid";
