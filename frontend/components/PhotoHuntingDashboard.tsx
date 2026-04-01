"use client";

import { startTransition, useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { fetchAnalysisStats, fetchLibraryStats, fetchMapPoints, fetchMedia, fetchMediaMonths, fetchRecentMedia, fetchScanStatus, fetchTripSummaries, searchMedia } from "../lib/api";
import type { AnalysisStats, LibraryStats, LibraryYear, MapResponse, MediaCard, MediaDetail, ScanStatus, SearchRequest, TimelineMonth, TripSummary, YearMediaGroup } from "../lib/types";
import { DiscoveryMap } from "./DiscoveryMap";
import { InspectorDrawer } from "./InspectorDrawer";
import { ResultGrid } from "./ResultGrid";

const START_YEAR = 2000;
const END_YEAR = new Date().getFullYear();
const COUNT_FORMATTER = new Intl.NumberFormat();
const SCAN_POLL_MS = 5000;
const BROWSE_LIMIT = 180;
const BROWSE_OVERVIEW_LIMIT = 50;
const MONTH_INITIAL_LIMIT = 30;
const TRIP_INITIAL_LIMIT = 50;
type ScrubberItem = {
  key: string;
  label: string;
  shortLabel: string;
  progress: number;
  anchorId: string;
  kind: "year" | "month";
  year: number;
  monthKey: string;
};

type IdleSchedulerWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function formatStat(value: number | null, loading: boolean): string {
  if (loading) {
    return "Loading...";
  }
  if (value === null) {
    return "—";
  }
  return COUNT_FORMATTER.format(value);
}

function formatSearchSummary(count: number): string {
  const label = count === 1 ? "photo" : "photos";
  return `${COUNT_FORMATTER.format(count)} ${label} matched`;
}

function formatSearchProgressSummary(visibleCount: number, totalCount: number): string {
  if (totalCount <= visibleCount) {
    return formatSearchSummary(totalCount);
  }
  return `Showing ${COUNT_FORMATTER.format(visibleCount)} of ${COUNT_FORMATTER.format(totalCount)} matched photos`;
}

function formatBrowseProgressSummary(visibleCount: number, totalCount: number): string {
  if (totalCount <= visibleCount) {
    return `${COUNT_FORMATTER.format(totalCount)} photos`;
  }
  return `Showing ${COUNT_FORMATTER.format(visibleCount)} of ${COUNT_FORMATTER.format(totalCount)} photos`;
}

function humanizeMode(mode: string | null | undefined): string | null {
  if (!mode) {
    return null;
  }
  return mode
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatTimestamp(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function basename(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? value;
}

function formatTripDate(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatYear(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.getFullYear();
}

function formatMonthKey(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}`;
}

function getMonthDateRange(monthKey: string): { dateFrom: string; dateTo: string } {
  const [yearText, monthText] = monthKey.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const lastDay = new Date(year, month, 0).getDate();
  return {
    dateFrom: `${monthKey}-01`,
    dateTo: `${monthKey}-${String(lastDay).padStart(2, "0")}`,
  };
}

function deriveYearsFromMonths(months: TimelineMonth[]): LibraryYear[] {
  const yearCounts = new Map<number, number>();
  for (const month of months) {
    yearCounts.set(month.year, (yearCounts.get(month.year) ?? 0) + month.count);
  }
  return [...yearCounts.entries()]
    .sort((left, right) => right[0] - left[0])
    .map(([year, count]) => ({ year, count }));
}

function ScanStatusIcon({ status }: { status: string }) {
  return (
    <svg aria-hidden="true" className="scanStatusIconSvg" viewBox="0 0 24 24">
      <path
        d="M6.5 7.5h2l1.2-2h4.6l1.2 2h2a2 2 0 0 1 2 2v6.8a2.2 2.2 0 0 1-2.2 2.2H6.2A2.2 2.2 0 0 1 4 16.3V9.5a2 2 0 0 1 2-2Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <circle cx="12" cy="13" fill="none" r="3.35" stroke="currentColor" strokeWidth="1.8" />
      <circle className={`scanStatusPulse ${status}`} cx="18.2" cy="6.2" r="2.2" />
    </svg>
  );
}

function SparkStatusIcon({ status }: { status: string }) {
  return (
    <svg aria-hidden="true" className="scanStatusIconSvg" viewBox="0 0 24 24">
      <path
        d="m12 3 1.9 4.9L19 10l-5.1 2.1L12 17l-1.9-4.9L5 10l5.1-2.1L12 3Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <circle className={`scanStatusPulse ${status}`} cx="18.2" cy="6.2" r="2.2" />
    </svg>
  );
}

export function PhotoHuntingDashboard() {
  const [query, setQuery] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [aiCompletedOnly, setAiCompletedOnly] = useState(true);
  const [activePane, setActivePane] = useState<"photos" | "map" | "details" | "trips">("photos");
  const [gridDensity, setGridDensity] = useState<"comfortable" | "compact">("compact");
  const [showSidebarPanel, setShowSidebarPanel] = useState(false);
  const [showInfoPanel, setShowInfoPanel] = useState(false);
  const [searchMode, setSearchMode] = useState<"ai" | "classic">("ai");
  const [country, setCountry] = useState("");
  const [city, setCity] = useState("");
  const [tag, setTag] = useState("");
  const [activeTripName, setActiveTripName] = useState<string | null>(null);
  const [bbox, setBbox] = useState<string | undefined>(undefined);
  const [mapZoom, setMapZoom] = useState(2);
  const [mapViewportReady, setMapViewportReady] = useState(false);
  const [yearStart, setYearStart] = useState(START_YEAR);
  const [yearEnd, setYearEnd] = useState(END_YEAR);
  const [showRoutes, setShowRoutes] = useState(true);
  const [libraryStats, setLibraryStats] = useState<LibraryStats | null>(null);
  const [libraryYears, setLibraryYears] = useState<LibraryYear[]>([]);
  const [timelineMonths, setTimelineMonths] = useState<TimelineMonth[]>([]);
  const [browseYear, setBrowseYear] = useState<number | null>(null);
  const [browseMonthKey, setBrowseMonthKey] = useState<string | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [analysisStats, setAnalysisStats] = useState<AnalysisStats | null>(null);
  const [tripSummaries, setTripSummaries] = useState<TripSummary[]>([]);
  const [results, setResults] = useState<MediaCard[]>([]);
  const [yearGroups, setYearGroups] = useState<YearMediaGroup[]>([]);
  const [resultMode, setResultMode] = useState<"browse" | "search">("browse");
  const [resultSummary, setResultSummary] = useState("Loading photos from your library...");
  const [searchTotalCount, setSearchTotalCount] = useState<number | null>(null);
  const [searchLoadingMore, setSearchLoadingMore] = useState(false);
  const [browseHasMore, setBrowseHasMore] = useState(false);
  const [browseLoadingMore, setBrowseLoadingMore] = useState(false);
  const [secondaryDataEnabled, setSecondaryDataEnabled] = useState(false);
  const [activeSearchRequest, setActiveSearchRequest] = useState<SearchRequest | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<MediaDetail | null>(null);
  const [resultsLoading, setResultsLoading] = useState(true);
  const [mapLoading, setMapLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [scanStatusLoading, setScanStatusLoading] = useState(true);
  const [analysisStatusLoading, setAnalysisStatusLoading] = useState(true);
  const [tripsLoading, setTripsLoading] = useState(false);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [scanStatusError, setScanStatusError] = useState<string | null>(null);
  const [analysisStatusError, setAnalysisStatusError] = useState<string | null>(null);
  const [tripsError, setTripsError] = useState<string | null>(null);
  const [mapResponse, setMapResponse] = useState<MapResponse>({ points: [], routes: [] });
  const [timelineHoverTop, setTimelineHoverTop] = useState<number | null>(null);
  const [scrubberDragging, setScrubberDragging] = useState(false);
  const [visibleTimelineMonthKey, setVisibleTimelineMonthKey] = useState<string | null>(null);
  const [visibleTimelineYear, setVisibleTimelineYear] = useState<number | null>(null);
  const browseLoadMoreRef = useRef<HTMLDivElement | null>(null);
  const browseLoadControllerRef = useRef<AbortController | null>(null);
  const scrubberTrackRef = useRef<HTMLDivElement | null>(null);
  const scrubberFadeTimeoutRef = useRef<number | null>(null);
  const scrubberPreviewKeyRef = useRef<string | null>(null);
  const focusInputFrameRef = useRef<number | null>(null);
  const themeImageRef = useRef<HTMLImageElement | null>(null);
  const boundedYearStart = Math.min(yearStart, yearEnd);
  const boundedYearEnd = Math.max(yearStart, yearEnd);
  const timelineMinYear = libraryYears.at(-1)?.year ?? START_YEAR;
  const timelineMaxYear = libraryYears[0]?.year ?? END_YEAR;
  const resetTimelineMinYear = libraryYears.at(-1)?.year ?? libraryStats?.available_years?.at(-1)?.year ?? START_YEAR;
  const resetTimelineMaxYear = libraryYears[0]?.year ?? libraryStats?.available_years?.[0]?.year ?? END_YEAR;
  const analysisStatus = aiCompletedOnly ? "completed" : null;
  const selectedBrowseYearEntry = useMemo(
    () => (browseYear ? libraryYears.find((entry) => entry.year === browseYear) ?? null : null),
    [browseYear, libraryYears],
  );
  const selectedBrowseMonthEntry = useMemo(
    () => (browseMonthKey ? timelineMonths.find((entry) => entry.key === browseMonthKey) ?? null : null),
    [browseMonthKey, timelineMonths],
  );
  const browseContextKey = useMemo(
    () => JSON.stringify({
      resultMode,
      browseYear,
      browseMonthKey,
      mediaType,
      analysisStatus,
      tripName: activeTripName,
      country,
      city,
      tag,
    }),
    [activeTripName, analysisStatus, browseMonthKey, browseYear, city, country, mediaType, resultMode, tag],
  );
  const activeTripPhotoCount = useMemo(() => {
    if (!activeTripName || !timelineMonths.length) {
      return null;
    }
    return timelineMonths.reduce((total, month) => total + month.count, 0);
  }, [activeTripName, timelineMonths]);
  const activeBrowseLimit = browseMonthKey ? MONTH_INITIAL_LIMIT : activeTripName ? TRIP_INITIAL_LIMIT : BROWSE_LIMIT;
  const themeSource = selectedDetail?.thumbnail_url ?? results[0]?.thumbnail_url ?? null;

  const focusSearchInput = useCallback(() => {
    if (focusInputFrameRef.current !== null) {
      window.cancelAnimationFrame(focusInputFrameRef.current);
    }
    focusInputFrameRef.current = window.requestAnimationFrame(() => {
      focusInputFrameRef.current = null;
      document.getElementById("query")?.focus();
    });
  }, []);

  const buildSearchRequest = useCallback((nextQuery: string): SearchRequest => ({
    query: nextQuery.trim(),
    media_type: mediaType || null,
    analysis_status: analysisStatus,
    trip_name: activeTripName,
    tags: tag ? [tag] : [],
    country: country || null,
    city: city || null,
    bbox: bbox ?? null,
    date_from: `${boundedYearStart}-01-01`,
    date_to: `${boundedYearEnd}-12-31`,
    offset: 0,
    limit: 18,
  }), [activeTripName, analysisStatus, bbox, boundedYearEnd, boundedYearStart, city, country, mediaType, tag]);

  const searchIsDirty = useMemo(() => {
    if (!activeSearchRequest) {
      return false;
    }
    const latest = buildSearchRequest(query);
    return JSON.stringify(latest) !== JSON.stringify(activeSearchRequest);
  }, [activeSearchRequest, buildSearchRequest, query]);

  const buildBrowseYearSummary = useCallback((displayedCount: number) => {
    if (activeTripName && !browseYear && !browseMonthKey) {
      return activeTripPhotoCount && activeTripPhotoCount > displayedCount
        ? `Showing ${COUNT_FORMATTER.format(displayedCount)} of ${COUNT_FORMATTER.format(activeTripPhotoCount)} photos from ${activeTripName}.`
        : `Showing ${COUNT_FORMATTER.format(displayedCount)} photos from ${activeTripName}.`;
    }
    if (!browseYear) {
      return displayedCount
        ? `Showing ${COUNT_FORMATTER.format(displayedCount)} photos.`
        : "Browsing your library.";
    }
    if (selectedBrowseMonthEntry) {
      return displayedCount < selectedBrowseMonthEntry.count
        ? `Showing ${COUNT_FORMATTER.format(displayedCount)} of ${COUNT_FORMATTER.format(selectedBrowseMonthEntry.count)} photos from ${selectedBrowseMonthEntry.label}.`
        : `Showing ${COUNT_FORMATTER.format(displayedCount)} photos from ${selectedBrowseMonthEntry.label}.`;
    }
    if (selectedBrowseYearEntry) {
      return displayedCount < selectedBrowseYearEntry.count
        ? `Showing ${COUNT_FORMATTER.format(displayedCount)} of ${COUNT_FORMATTER.format(selectedBrowseYearEntry.count)} photos from ${browseYear}.`
        : `Showing ${COUNT_FORMATTER.format(displayedCount)} photos from ${browseYear}.`;
    }
    return `Showing ${COUNT_FORMATTER.format(displayedCount)} photos from ${browseYear}.`;
  }, [activeTripName, activeTripPhotoCount, browseMonthKey, browseYear, selectedBrowseMonthEntry, selectedBrowseYearEntry]);

  const handleViewportChange = useCallback((nextBbox: string, nextZoom: number) => {
    setMapViewportReady(true);
    setMapZoom((current) => (current === nextZoom ? current : nextZoom));
    setBbox((current) => (current === nextBbox ? current : nextBbox));
  }, []);

  const handleTimelineHover = useCallback((_label: string, progress: number) => {
    if (scrubberFadeTimeoutRef.current !== null) {
      window.clearTimeout(scrubberFadeTimeoutRef.current);
      scrubberFadeTimeoutRef.current = null;
    }
    setTimelineHoverTop(progress);
  }, []);

  const clearTimelineHover = useCallback((delayMs = 0) => {
    if (scrubberFadeTimeoutRef.current !== null) {
      window.clearTimeout(scrubberFadeTimeoutRef.current);
      scrubberFadeTimeoutRef.current = null;
    }
    if (delayMs > 0) {
      scrubberFadeTimeoutRef.current = window.setTimeout(() => {
        setTimelineHoverTop(null);
        scrubberFadeTimeoutRef.current = null;
      }, delayMs);
      return;
    }
    setTimelineHoverTop(null);
  }, []);

  const scrollToTimelineAnchor = useCallback((anchorId: string, behavior: ScrollBehavior = "auto") => {
    const target = document.getElementById(anchorId);
    if (!target) {
      return false;
    }
    target.scrollIntoView({ behavior, block: "start" });
    return true;
  }, []);

  useEffect(() => () => {
    browseLoadControllerRef.current?.abort();
    if (scrubberFadeTimeoutRef.current !== null) {
      window.clearTimeout(scrubberFadeTimeoutRef.current);
    }
    if (focusInputFrameRef.current !== null) {
      window.cancelAnimationFrame(focusInputFrameRef.current);
    }
    if (themeImageRef.current) {
      themeImageRef.current.onload = null;
      themeImageRef.current.onerror = null;
      themeImageRef.current.src = "";
      themeImageRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (resultMode === "browse" && browseYear) {
      setResultSummary(buildBrowseYearSummary(results.length));
    }
  }, [browseYear, buildBrowseYearSummary, resultMode, results.length]);

  useEffect(() => {
    if (resultMode === "browse" && activeTripName && !browseYear && !browseMonthKey && results.length) {
      setResultSummary(buildBrowseYearSummary(results.length));
    }
  }, [activeTripName, browseMonthKey, browseYear, buildBrowseYearSummary, resultMode, results.length]);

  useEffect(() => {
    if (resultMode === "browse" && !activeTripName && !browseYear && !browseMonthKey && results.length) {
      setResultSummary(buildBrowseYearSummary(results.length));
    }
  }, [activeTripName, browseMonthKey, browseYear, buildBrowseYearSummary, resultMode, results.length]);

  useEffect(() => {
    clearTimelineHover();
  }, [browseMonthKey, browseYear, clearTimelineHover, resultMode, results.length, timelineMonths.length]);

  useEffect(() => {
    if (browseMonthKey && !timelineMonths.some((entry) => entry.key === browseMonthKey)) {
      setBrowseMonthKey(null);
    }
  }, [browseMonthKey, timelineMonths]);

  useEffect(() => {
    if (resultMode !== "browse" || browseYear) {
      setVisibleTimelineYear(null);
      return;
    }

    let frameId = 0;
    const updateVisibleYear = () => {
      const sections = Array.from(document.querySelectorAll<HTMLElement>(".yearSection[id^='year-']"));
      if (!sections.length) {
        setVisibleTimelineYear(null);
        return;
      }

      const anchorOffset = 180;
      let nextYear = Number(sections[0].id.replace(/^year-/, ""));
      for (const section of sections) {
        const rect = section.getBoundingClientRect();
        if (rect.top - anchorOffset <= 0) {
          nextYear = Number(section.id.replace(/^year-/, ""));
          continue;
        }
        break;
      }

      setVisibleTimelineYear((current) => (current === nextYear ? current : nextYear));
    };

    const handleViewportChange = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(updateVisibleYear);
    };

    updateVisibleYear();
    window.addEventListener("scroll", handleViewportChange, { passive: true });
    window.addEventListener("resize", handleViewportChange);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("scroll", handleViewportChange);
      window.removeEventListener("resize", handleViewportChange);
    };
  }, [browseYear, resultMode, results.length, yearGroups.length]);

  useEffect(() => {
    if (resultMode !== "browse") {
      setVisibleTimelineMonthKey(null);
      return;
    }

    let frameId = 0;
    const updateVisibleMonth = () => {
      const sections = Array.from(document.querySelectorAll<HTMLElement>(".feedMonthSection[id^='month-']"));
      if (!sections.length) {
        setVisibleTimelineMonthKey(null);
        return;
      }

      const anchorOffset = 140;
      let nextKey = sections[0].id.replace(/^month-/, "");
      for (const section of sections) {
        const rect = section.getBoundingClientRect();
        if (rect.top - anchorOffset <= 0) {
          nextKey = section.id.replace(/^month-/, "");
          continue;
        }
        break;
      }

      setVisibleTimelineMonthKey((current) => (current === nextKey ? current : nextKey));
    };

    const handleViewportChange = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(updateVisibleMonth);
    };

    updateVisibleMonth();
    window.addEventListener("scroll", handleViewportChange, { passive: true });
    window.addEventListener("resize", handleViewportChange);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("scroll", handleViewportChange);
      window.removeEventListener("resize", handleViewportChange);
    };
  }, [resultMode, results.length]);

  useEffect(() => {
    if (!themeSource) {
      return;
    }

    let cancelled = false;
    const image = new Image();
    themeImageRef.current = image;
    image.crossOrigin = "anonymous";
    image.decoding = "async";
    image.referrerPolicy = "no-referrer";

    const cleanupImage = () => {
      image.onload = null;
      image.onerror = null;
      if (themeImageRef.current === image) {
        themeImageRef.current = null;
      }
      image.src = "";
    };

    image.onload = () => {
      if (cancelled) {
        return;
      }
      const canvas = document.createElement("canvas");
      const width = 24;
      const height = 24;
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) {
        return;
      }
      context.drawImage(image, 0, 0, width, height);
      const data = context.getImageData(0, 0, width, height).data;

      let redTotal = 0;
      let greenTotal = 0;
      let blueTotal = 0;
      let samples = 0;

      for (let index = 0; index < data.length; index += 4) {
        const alpha = data[index + 3];
        if (alpha < 128) {
          continue;
        }
        redTotal += data[index];
        greenTotal += data[index + 1];
        blueTotal += data[index + 2];
        samples += 1;
      }

      if (!samples) {
        return;
      }

      const red = Math.round(redTotal / samples);
      const green = Math.round(greenTotal / samples);
      const blue = Math.round(blueTotal / samples);
      const root = document.documentElement;

      root.style.setProperty("--theme-rgb", `${red}, ${green}, ${blue}`);
      root.style.setProperty("--theme-accent", `rgb(${red} ${green} ${blue})`);
      root.style.setProperty("--theme-accent-soft", `rgba(${red}, ${green}, ${blue}, 0.16)`);
      root.style.setProperty("--theme-glow", `rgba(${red}, ${green}, ${blue}, 0.3)`);

      canvas.width = 0;
      canvas.height = 0;
    };

    image.onerror = cleanupImage;
    image.src = themeSource;

    return () => {
      cancelled = true;
      cleanupImage();
    };
  }, [themeSource]);

  useEffect(() => {
    if (!libraryYears.length) {
      return;
    }
    const minAvailableYear = libraryYears.at(-1)?.year ?? START_YEAR;
    const maxAvailableYear = libraryYears[0]?.year ?? END_YEAR;
    setYearStart((current) => Math.min(maxAvailableYear, Math.max(minAvailableYear, current)));
    setYearEnd((current) => Math.min(maxAvailableYear, Math.max(minAvailableYear, current)));
    setBrowseYear((current) => (
      current && libraryYears.some((entry) => entry.year === current)
        ? current
        : null
    ));
  }, [libraryYears]);

  useEffect(() => {
    if (secondaryDataEnabled || resultsLoading) {
      return;
    }
    const idleWindow = window as IdleSchedulerWindow;
    let timeoutId: number | null = null;
    let idleId: number | null = null;
    if (typeof idleWindow.requestIdleCallback === "function") {
      idleId = idleWindow.requestIdleCallback(() => {
        setSecondaryDataEnabled(true);
      }, { timeout: 350 });
    } else {
      timeoutId = window.setTimeout(() => {
        setSecondaryDataEnabled(true);
      }, 250);
    }
    return () => {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      if (idleId !== null && typeof idleWindow.cancelIdleCallback === "function") {
        idleWindow.cancelIdleCallback(idleId);
      }
    };
  }, [resultsLoading, secondaryDataEnabled]);

  useEffect(() => {
    if (!secondaryDataEnabled) {
      return;
    }
    let active = true;
    let controller: AbortController | null = null;
    let firstLoad = true;

    const loadAnalysisStats = () => {
      if (document.hidden) {
        return;
      }
      controller?.abort();
      controller = new AbortController();
      if (firstLoad) {
        setAnalysisStatusLoading(true);
      }
      setAnalysisStatusError(null);

      fetchAnalysisStats(controller.signal)
        .then((stats) => {
          if (!active || controller?.signal.aborted) {
            return;
          }
          startTransition(() => {
            setAnalysisStats(stats);
          });
        })
        .catch((reason: unknown) => {
          if (isAbortError(reason) || !active) {
            return;
          }
          setAnalysisStatusError(reason instanceof Error ? reason.message : "Unable to load AI analysis status.");
        })
        .finally(() => {
          if (active && !controller?.signal.aborted) {
            setAnalysisStatusLoading(false);
            firstLoad = false;
          }
        });
    };

    loadAnalysisStats();
    const intervalId = window.setInterval(loadAnalysisStats, SCAN_POLL_MS);

    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(intervalId);
    };
  }, [secondaryDataEnabled]);

  const applySelection = useCallback((items: MediaCard[]) => {
    setSelectedId((current) => {
      if (current && !items.some((item) => item.id === current)) {
        return null;
      }
      return current;
    });
  }, []);

  const handleSelectMedia = useCallback((mediaId: string) => {
    setSelectedId(mediaId);
    if (window.innerWidth < 1200) {
      setActivePane("details");
    }
  }, []);

  const handleOpenMedia = useCallback((mediaId: string) => {
    setSelectedId(mediaId);
    setActivePane("details");
  }, []);

  const handleMapPreview = useCallback((mediaId: string) => {
    setSelectedId(mediaId);
  }, []);

  const returnToAllYears = useCallback(() => {
    setResultMode("browse");
    setActiveSearchRequest(null);
    setSearchTotalCount(null);
    setSearchLoadingMore(false);
    setResultsError(null);
    setActivePane("photos");
    setBrowseYear(null);
    setBrowseMonthKey(null);
    setYearStart(resetTimelineMinYear);
    setYearEnd(resetTimelineMaxYear);
    setShowSidebarPanel(false);
  }, [resetTimelineMaxYear, resetTimelineMinYear]);

  const showRecent = useCallback(() => {
    returnToAllYears();
    setActiveTripName(null);
  }, [returnToAllYears]);

  const handleFilterTrip = useCallback((tripName: string) => {
    setActiveTripName((current) => current === tripName ? null : tripName);
    setResultMode("browse");
    setActiveSearchRequest(null);
    setSearchTotalCount(null);
    setSearchLoadingMore(false);
    setResultsError(null);
    setActivePane("photos");
    setBrowseMonthKey(null);
    setShowSidebarPanel(false);
  }, []);

  const submitSearch = useCallback((nextQuery?: string) => {
    const request = buildSearchRequest(nextQuery ?? query);
    if (!request.query) {
      showRecent();
      return;
    }
    setQuery(request.query);
    setResultMode("search");
    setActiveSearchRequest(request);
    setSearchTotalCount(null);
    setSearchLoadingMore(false);
    setResultsError(null);
    setActivePane("photos");
    setShowSidebarPanel(false);
  }, [buildSearchRequest, query, showRecent]);

  useEffect(() => {
    if (!secondaryDataEnabled) {
      return;
    }
    const controller = new AbortController();
    setStatsLoading(true);
    setStatsError(null);

    fetchLibraryStats(controller.signal)
      .then((stats) => {
        startTransition(() => {
          setLibraryStats(stats);
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setStatsError(reason instanceof Error ? reason.message : "Unable to load library stats.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setStatsLoading(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [secondaryDataEnabled]);

  useEffect(() => {
    if (!secondaryDataEnabled) {
      return;
    }
    const controller = new AbortController();
    fetchMediaMonths(
      {
        mediaType,
        analysisStatus: analysisStatus ?? undefined,
        tripName: activeTripName ?? undefined,
        country,
        city,
        tag,
        dateFrom: `${boundedYearStart}-01-01`,
        dateTo: `${boundedYearEnd}-12-31`,
      },
      controller.signal,
    )
      .then((months) => {
        if (!controller.signal.aborted) {
          startTransition(() => {
            setTimelineMonths(months);
            setLibraryYears(deriveYearsFromMonths(months));
          });
        }
      })
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) {
          console.error(reason);
        }
      });

    return () => {
      controller.abort();
    };
  }, [activeTripName, analysisStatus, boundedYearEnd, boundedYearStart, city, country, mediaType, secondaryDataEnabled, tag]);

  useEffect(() => {
    if (activePane !== "trips") {
      return;
    }

    const controller = new AbortController();
    setTripsLoading(true);
    setTripsError(null);

    fetchTripSummaries(
      {
        limit: 320,
        mediaType,
        analysisStatus: analysisStatus ?? undefined,
        country,
        city,
        tag,
        dateFrom: `${boundedYearStart}-01-01`,
        dateTo: `${boundedYearEnd}-12-31`,
      },
      controller.signal,
    )
      .then((items) => {
        if (controller.signal.aborted) {
          return;
        }
        startTransition(() => {
          setTripSummaries(items);
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setTripsError(reason instanceof Error ? reason.message : "Unable to load trips.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setTripsLoading(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [activePane, analysisStatus, boundedYearEnd, boundedYearStart, city, country, mediaType, tag]);

  useEffect(() => {
    if (!secondaryDataEnabled) {
      return;
    }
    let active = true;
    let controller: AbortController | null = null;
    let firstLoad = true;

    const loadScanStatus = () => {
      if (document.hidden) {
        return;
      }
      controller?.abort();
      controller = new AbortController();
      if (firstLoad) {
        setScanStatusLoading(true);
      }
      setScanStatusError(null);

      fetchScanStatus(controller.signal)
        .then((status) => {
          if (!active || controller?.signal.aborted) {
            return;
          }
          startTransition(() => {
            setScanStatus(status);
          });
        })
        .catch((reason: unknown) => {
          if (isAbortError(reason) || !active) {
            return;
          }
          setScanStatusError(reason instanceof Error ? reason.message : "Unable to load scan status.");
        })
        .finally(() => {
          if (active && !controller?.signal.aborted) {
            setScanStatusLoading(false);
            firstLoad = false;
          }
        });
    };

    loadScanStatus();
    const intervalId = window.setInterval(loadScanStatus, SCAN_POLL_MS);

    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(intervalId);
    };
  }, [secondaryDataEnabled]);

  useEffect(() => {
    if (resultMode !== "browse") {
      setBrowseHasMore(false);
      setBrowseLoadingMore(false);
      browseLoadControllerRef.current?.abort();
      return;
    }
    setSearchTotalCount(null);
    setSearchLoadingMore(false);
    const controller = new AbortController();
    browseLoadControllerRef.current?.abort();
    setBrowseLoadingMore(false);
    setResultsLoading(true);
    setResultsError(null);

    if (!browseMonthKey && !browseYear && !activeTripName) {
      setBrowseHasMore(false);
      fetchRecentMedia(
        {
          limit: BROWSE_OVERVIEW_LIMIT,
          mediaType,
          analysisStatus: analysisStatus ?? undefined,
          tripName: activeTripName ?? undefined,
          country,
          city,
          tag,
          dateFrom: `${boundedYearStart}-01-01`,
          dateTo: `${boundedYearEnd}-12-31`,
        },
        controller.signal,
      )
        .then((items) => {
          if (controller.signal.aborted) return;
          startTransition(() => {
            setYearGroups([]);
            setResults(items);
            applySelection(items);
            setResultSummary(`Showing ${COUNT_FORMATTER.format(items.length)} recently added photos.`);
          });
        })
        .catch((reason: unknown) => {
          if (isAbortError(reason)) {
            return;
          }
          setResultsError(reason instanceof Error ? reason.message : "Unable to load library overview.");
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setResultsLoading(false);
          }
        });
      return () => {
        controller.abort();
      };
    }

    const browseRange = browseMonthKey
      ? getMonthDateRange(browseMonthKey)
      : browseYear
        ? {
            dateFrom: `${browseYear}-01-01`,
            dateTo: `${browseYear}-12-31`,
          }
        : {
            dateFrom: `${boundedYearStart}-01-01`,
            dateTo: `${boundedYearEnd}-12-31`,
          };

    fetchRecentMedia(
      {
        limit: activeBrowseLimit,
        offset: 0,
        mediaType,
        analysisStatus: analysisStatus ?? undefined,
        tripName: activeTripName ?? undefined,
        country,
        city,
        tag,
        dateFrom: browseRange.dateFrom,
        dateTo: browseRange.dateTo,
      },
      controller.signal,
    )
      .then((items) => {
        if (controller.signal.aborted) return;
        startTransition(() => {
          setYearGroups([]);
          setResults(items);
          applySelection(items);
          setBrowseHasMore(
            selectedBrowseMonthEntry
              ? items.length < selectedBrowseMonthEntry.count
              : selectedBrowseYearEntry
                ? items.length < selectedBrowseYearEntry.count
                : activeTripPhotoCount !== null
                  ? items.length < activeTripPhotoCount
                  : items.length === activeBrowseLimit,
          );
          setResultSummary(buildBrowseYearSummary(items.length));
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setResultsError(reason instanceof Error ? reason.message : "Unable to load library photos.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setResultsLoading(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [activeBrowseLimit, activeTripName, activeTripPhotoCount, analysisStatus, applySelection, boundedYearEnd, boundedYearStart, browseMonthKey, browseYear, buildBrowseYearSummary, city, country, mediaType, resultMode, selectedBrowseMonthEntry, selectedBrowseYearEntry, tag]);

  const loadMoreBrowseYear = useCallback(() => {
    if (resultMode !== "browse" || (!browseMonthKey && !browseYear && !activeTripName) || !browseHasMore || browseLoadingMore || resultsLoading) {
      return;
    }

    const controller = new AbortController();
    const requestContext = browseContextKey;
    browseLoadControllerRef.current?.abort();
    browseLoadControllerRef.current = controller;
    setBrowseLoadingMore(true);

    fetchRecentMedia(
      {
        limit: activeBrowseLimit,
        offset: results.length,
        mediaType,
        analysisStatus: analysisStatus ?? undefined,
        tripName: activeTripName ?? undefined,
        country,
        city,
        tag,
        dateFrom: (
          browseMonthKey
            ? getMonthDateRange(browseMonthKey)
            : browseYear
              ? { dateFrom: `${browseYear}-01-01`, dateTo: `${browseYear}-12-31` }
              : { dateFrom: `${boundedYearStart}-01-01`, dateTo: `${boundedYearEnd}-12-31` }
        ).dateFrom,
        dateTo: (
          browseMonthKey
            ? getMonthDateRange(browseMonthKey)
            : browseYear
              ? { dateFrom: `${browseYear}-01-01`, dateTo: `${browseYear}-12-31` }
              : { dateFrom: `${boundedYearStart}-01-01`, dateTo: `${boundedYearEnd}-12-31` }
        ).dateTo,
      },
      controller.signal,
    )
      .then((items) => {
        if (controller.signal.aborted || browseContextKey !== requestContext) {
          return;
        }

        const existingIds = new Set(results.map((item) => item.id));
        const appendedItems = items.filter((item) => !existingIds.has(item.id));
        const nextCount = results.length + appendedItems.length;

        startTransition(() => {
          setResults((current) => [...current, ...appendedItems]);
          if (selectedBrowseMonthEntry) {
            setBrowseHasMore(nextCount < selectedBrowseMonthEntry.count && appendedItems.length > 0);
          } else if (selectedBrowseYearEntry) {
            setBrowseHasMore(nextCount < selectedBrowseYearEntry.count && appendedItems.length > 0);
          } else if (activeTripPhotoCount !== null) {
            setBrowseHasMore(nextCount < activeTripPhotoCount && appendedItems.length > 0);
          } else {
            setBrowseHasMore(appendedItems.length === activeBrowseLimit);
          }
          setResultSummary(buildBrowseYearSummary(nextCount));
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setResultsError(reason instanceof Error ? reason.message : "Unable to load more library photos.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setBrowseLoadingMore(false);
        }
      });
  }, [activeBrowseLimit, activeTripName, activeTripPhotoCount, analysisStatus, boundedYearEnd, boundedYearStart, browseContextKey, browseHasMore, browseLoadingMore, browseMonthKey, browseYear, buildBrowseYearSummary, city, country, mediaType, resultMode, results, resultsLoading, selectedBrowseMonthEntry, selectedBrowseYearEntry, tag]);

  useEffect(() => {
    if (activeTripName || browseMonthKey || resultMode !== "browse" || (!browseMonthKey && !browseYear) || !browseHasMore || resultsLoading || browseLoadingMore) {
      return;
    }

    const node = browseLoadMoreRef.current;
    if (!node) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          loadMoreBrowseYear();
        }
      },
      { rootMargin: "360px 0px" },
    );

    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, [activeTripName, browseHasMore, browseLoadingMore, browseMonthKey, browseYear, loadMoreBrowseYear, resultMode, results.length, resultsLoading]);

  useEffect(() => {
    if (!activeSearchRequest || resultMode !== "search") {
      return;
    }
    const controller = new AbortController();
    setResultsLoading(true);
    setResultsError(null);

    searchMedia(activeSearchRequest, controller.signal)
      .then((searchResult) => {
        if (controller.signal.aborted) return;
        startTransition(() => {
          setResults(searchResult.results);
          applySelection(searchResult.results);
          setSearchTotalCount(searchResult.total_count);
          setResultSummary(formatSearchProgressSummary(searchResult.results.length, searchResult.total_count));
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setResultsError(reason instanceof Error ? reason.message : "Unable to load ranked matches.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setResultsLoading(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [activeSearchRequest, applySelection, resultMode]);

  const loadMoreSearchResults = useCallback(() => {
    if (
      resultMode !== "search"
      || !activeSearchRequest
      || searchLoadingMore
      || resultsLoading
      || searchTotalCount === null
      || results.length >= searchTotalCount
    ) {
      return;
    }

    const controller = new AbortController();
    setSearchLoadingMore(true);

    searchMedia(
      {
        ...activeSearchRequest,
        offset: results.length,
        limit: activeSearchRequest.limit ?? 18,
      },
      controller.signal,
    )
      .then((searchResult) => {
        if (controller.signal.aborted) {
          return;
        }
        const existingIds = new Set(results.map((item) => item.id));
        const appendedItems = searchResult.results.filter((item) => !existingIds.has(item.id));
        const nextVisibleCount = results.length + appendedItems.length;
        const nextTotalCount = appendedItems.length ? searchResult.total_count : nextVisibleCount;

        startTransition(() => {
          setResults((current) => [...current, ...appendedItems]);
          setSearchTotalCount(nextTotalCount);
          setResultSummary(formatSearchProgressSummary(nextVisibleCount, nextTotalCount));
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setResultsError(reason instanceof Error ? reason.message : "Unable to load more ranked matches.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSearchLoadingMore(false);
        }
      });
  }, [activeSearchRequest, resultMode, results, resultsLoading, searchLoadingMore, searchTotalCount]);

  useEffect(() => {
    if (!mapViewportReady) {
      return;
    }
    const controller = new AbortController();
    setMapLoading(true);
    setMapError(null);

    const activeBrowseRange = browseMonthKey
      ? getMonthDateRange(browseMonthKey)
      : browseYear
        ? { dateFrom: `${browseYear}-01-01`, dateTo: `${browseYear}-12-31` }
        : null;
    const activeDateFrom = resultMode === "browse" && activeBrowseRange ? activeBrowseRange.dateFrom : `${boundedYearStart}-01-01`;
    const activeDateTo = resultMode === "browse" && activeBrowseRange ? activeBrowseRange.dateTo : `${boundedYearEnd}-12-31`;

    fetchMapPoints(
      {
        bbox,
        zoom: mapZoom,
        mediaType,
        analysisStatus: analysisStatus ?? undefined,
        tripName: activeTripName ?? undefined,
        country,
        city,
        tag,
        dateFrom: activeDateFrom,
        dateTo: activeDateTo,
      },
      controller.signal,
    )
      .then((mapResult) => {
        if (controller.signal.aborted) return;
        startTransition(() => {
          setMapResponse(mapResult);
        });
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        setMapError(reason instanceof Error ? reason.message : "Unable to load the map.");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setMapLoading(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [activeTripName, analysisStatus, bbox, browseMonthKey, browseYear, boundedYearEnd, boundedYearStart, city, country, mapViewportReady, mapZoom, mediaType, resultMode, tag]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedDetail(null);
      return;
    }
    const controller = new AbortController();
    fetchMedia(selectedId, controller.signal)
      .then((item) => {
        if (!controller.signal.aborted) {
          setSelectedDetail(item);
        }
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) {
          return;
        }
        if (!controller.signal.aborted) {
          setSelectedDetail(null);
        }
      });
    return () => {
      controller.abort();
    };
  }, [selectedId]);

  const pageError = resultsError ?? mapError ?? statsError;
  const indexedMediaValue = formatStat(libraryStats?.indexed_media ?? null, statsLoading && !libraryStats);
  const mappedMediaValue = formatStat(libraryStats?.mapped_media ?? null, statsLoading && !libraryStats);
  const tripRoutesValue = formatStat(libraryStats?.trip_routes ?? null, statsLoading && !libraryStats);
  const selectedBrowseYear = browseYear ?? libraryYears[0]?.year ?? null;
  const selectedYearEntry = libraryYears.find((entry) => entry.year === selectedBrowseYear);
  const resultPanelTitle = resultMode === "search"
    ? "Ranked media matches"
    : activeTripName
      ? activeTripName
      : selectedBrowseMonthEntry
        ? selectedBrowseMonthEntry.label
        : browseYear
          ? `${browseYear}`
          : "All photos";
  const activePhotoCount = resultMode === "browse" && selectedBrowseMonthEntry
    ? selectedBrowseMonthEntry.count
    : resultMode === "browse" && browseYear && selectedYearEntry
      ? selectedYearEntry.count
      : resultMode === "browse" && activeTripName && activeTripPhotoCount !== null
        ? activeTripPhotoCount
      : results.length;
  const mapSummary = mapLoading
    ? "Loading visible map points..."
    : `${COUNT_FORMATTER.format(mapResponse.points.length)} points in the current view`;
  const timelineYears = useMemo(() => libraryYears.map((entry) => entry.year), [libraryYears]);
  const activeTimelineYear = browseYear ?? visibleTimelineYear ?? formatYear(selectedDetail?.date_taken ?? results[0]?.date_taken ?? null);
  const activeTimelineMonthKey = browseYear
    ? browseMonthKey ?? visibleTimelineMonthKey ?? formatMonthKey(selectedDetail?.date_taken ?? results[0]?.date_taken ?? null)
    : null;
  const scrubberItems = useMemo<ScrubberItem[]>(() => {
    const source = timelineMonths.map((month, index) => {
      const isYearMarker = index === 0 || timelineMonths[index - 1]?.year !== month.year;
      return {
        key: isYearMarker ? `year-${month.year}` : month.key,
        label: isYearMarker ? String(month.year) : month.label,
        shortLabel: isYearMarker ? String(month.year) : month.short_label,
        anchorId: isYearMarker ? `year-${month.year}` : `month-${month.key}`,
        kind: isYearMarker ? "year" as const : "month" as const,
        year: month.year,
        monthKey: month.key,
      };
    });

    return source.map((item, index) => ({
      ...item,
      progress: source.length <= 1 ? 0.5 : index / (source.length - 1),
    }));
  }, [timelineMonths]);
  const activeScrubberKey = browseMonthKey
    ?? (browseYear ? `year-${browseYear}` : null)
    ?? activeTimelineMonthKey
    ?? formatMonthKey(selectedDetail?.date_taken ?? results[0]?.date_taken ?? null);
  const activeScrubberItem = useMemo(
    () => scrubberItems.find((item) => item.key === activeScrubberKey || item.monthKey === activeScrubberKey) ?? scrubberItems[0] ?? null,
    [activeScrubberKey, scrubberItems],
  );
  const scrubberHandleProgress = timelineHoverTop ?? activeScrubberItem?.progress ?? 0.5;
  const applyScrubberSelection = useCallback((item: ScrubberItem) => {
    scrubberPreviewKeyRef.current = null;
    if (item.kind === "year") {
      setBrowseYear(item.year);
      setBrowseMonthKey(null);
    } else {
      setBrowseYear(null);
      setBrowseMonthKey(item.monthKey);
    }
    setResultMode("browse");
    setActiveSearchRequest(null);
    setResultsError(null);
    setActivePane("photos");
  }, []);
  const resolveScrubberItem = useCallback((clientY: number) => {
    const track = scrubberTrackRef.current;
    if (!track || !scrubberItems.length) {
      return null;
    }

    const rect = track.getBoundingClientRect();
    const relativeY = Math.max(0, Math.min(clientY - rect.top, rect.height));
    const progress = rect.height > 0 ? relativeY / rect.height : 0;
    const index = scrubberItems.length === 1
      ? 0
      : Math.min(scrubberItems.length - 1, Math.max(0, Math.round(progress * (scrubberItems.length - 1))));

    return scrubberItems[index] ?? null;
  }, [scrubberItems]);
  const updateScrubberFromPointer = useCallback((clientY: number) => {
    const item = resolveScrubberItem(clientY);
    if (!item) {
      return;
    }
    scrubberPreviewKeyRef.current = item.key;
    handleTimelineHover(item.label, item.progress);
    scrollToTimelineAnchor(item.anchorId, "auto");
  }, [handleTimelineHover, resolveScrubberItem, scrollToTimelineAnchor]);
  const handleScrubberPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!scrubberItems.length) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setScrubberDragging(true);
    updateScrubberFromPointer(event.clientY);
  }, [scrubberItems.length, updateScrubberFromPointer]);
  const handleScrubberPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!scrubberDragging) {
      return;
    }
    event.preventDefault();
    updateScrubberFromPointer(event.clientY);
  }, [scrubberDragging, updateScrubberFromPointer]);
  const stopScrubber = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
    if (event) {
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer may already be released; no action needed.
      }
    }
    setScrubberDragging(false);
    const previewKey = scrubberPreviewKeyRef.current;
    if (previewKey) {
      const previewItem = scrubberItems.find((item) => item.key === previewKey);
      if (previewItem) {
        applyScrubberSelection(previewItem);
      }
    }
    clearTimelineHover(900);
  }, [applyScrubberSelection, clearTimelineHover, scrubberItems]);
  const scanTone = scanStatusError
    ? "warning"
    : scanStatus?.running
      ? "running"
      : scanStatus?.status === "stale"
        ? "warning"
        : "idle";
  const scanTooltipLines = [
    scanStatusLoading && !scanStatus ? "Checking background photo scan..." : null,
    scanStatusError ? scanStatusError : null,
    scanStatus?.running ? "Background photo scan is running." : null,
    !scanStatus?.running && !scanStatusError && !(scanStatusLoading && !scanStatus) ? "No background photo scan is active." : null,
    humanizeMode(scanStatus?.mode) ? `Mode: ${humanizeMode(scanStatus?.mode)}` : null,
    scanStatus?.event ? `Last event: ${scanStatus.event}` : null,
    scanStatus?.scanned != null ? `Scanned: ${COUNT_FORMATTER.format(scanStatus.scanned)}` : null,
    scanStatus?.updated != null ? `Updated: ${COUNT_FORMATTER.format(scanStatus.updated)}` : null,
    scanStatus?.created != null ? `Created: ${COUNT_FORMATTER.format(scanStatus.created)}` : null,
    scanStatus?.errors != null ? `Errors: ${COUNT_FORMATTER.format(scanStatus.errors)}` : null,
    basename(scanStatus?.last_path) ? `Current file: ${basename(scanStatus?.last_path)}` : null,
    formatTimestamp(scanStatus?.log_updated_at) ? `Last activity: ${formatTimestamp(scanStatus?.log_updated_at)}` : null,
    scanStatus?.detail ?? null,
  ].filter(Boolean) as string[];
  const aiTone = analysisStatusError
    ? "warning"
    : (analysisStats?.claimed ?? 0) > 0 || (analysisStats?.active_workers ?? 0) > 0
      ? "running"
      : (analysisStats?.failed ?? 0) > 0
        ? "warning"
        : "idle";
  const aiTooltipLines = [
    analysisStatusLoading && !analysisStats ? "Checking background AI analysis..." : null,
    analysisStatusError ? analysisStatusError : null,
    (analysisStats?.claimed ?? 0) > 0 || (analysisStats?.active_workers ?? 0) > 0 ? "Mac Florence worker is actively analyzing images." : null,
    !(analysisStats?.claimed ?? 0) && !(analysisStats?.active_workers ?? 0) && !analysisStatusError && !(analysisStatusLoading && !analysisStats)
      ? "No AI worker is actively analyzing images right now."
      : null,
    analysisStats?.active_workers != null ? `Active workers: ${COUNT_FORMATTER.format(analysisStats.active_workers)}` : null,
    analysisStats?.claimed != null ? `Claimed now: ${COUNT_FORMATTER.format(analysisStats.claimed)}` : null,
    analysisStats?.pending != null ? `Pending images: ${COUNT_FORMATTER.format(analysisStats.pending)}` : null,
    analysisStats?.completed != null ? `AI completed: ${COUNT_FORMATTER.format(analysisStats.completed)}` : null,
    analysisStats?.failed != null ? `Failed: ${COUNT_FORMATTER.format(analysisStats.failed)}` : null,
    analysisStats?.stale_claims != null ? `Stale claims: ${COUNT_FORMATTER.format(analysisStats.stale_claims)}` : null,
  ].filter(Boolean) as string[];
  const surfaceError = pageError;
  const photoSummary = resultsLoading
    ? "Loading..."
    : resultMode === "search"
      ? (searchTotalCount === null ? resultSummary : formatSearchProgressSummary(results.length, searchTotalCount))
      : browseMonthKey || activeTripName || (browseYear && selectedYearEntry)
        ? formatBrowseProgressSummary(results.length, activePhotoCount)
        : `${COUNT_FORMATTER.format(results.length)} photos in view`;
  const searchPlaceholder = searchMode === "ai" ? "Ask anything about your memories..." : "Search by date, place, or person...";

  return (
    <main className={`gpShell memoryStreamShell ${searchMode === "ai" ? "aiSearchMode" : "classicSearchMode"}`}>
      <header className="gpTopBar memoryTopBar">
        <div className="gpBrand">
          <img
            alt="Photo Hunting logo"
            className="gpBrandLogo"
            decoding="async"
            height={52}
            src="/photohunting-badge.png"
            width={52}
          />
          <div className="gpBrandText">
            <span className="gpBrandMark">Photo</span>
            <span>Hunting</span>
          </div>
        </div>
        <div className={`gpSearchBar memorySearchBar ${searchMode === "ai" ? "aiMode" : ""}`}>
          <button
            aria-label={`Switch to ${searchMode === "ai" ? "classic" : "AI"} search`}
            className={`searchModeOrb ${searchMode === "ai" ? "active" : ""}`}
            onClick={() => setSearchMode((current) => current === "ai" ? "classic" : "ai")}
            type="button"
          >
            {searchMode === "ai" ? "✦" : "⌕"}
          </button>
          <label className="srOnly" htmlFor="query">
            Search your memories
          </label>
          <input
            className="gpSearchInput"
            id="query"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submitSearch();
              }
            }}
            placeholder={searchPlaceholder}
            type="text"
            value={query}
          />
          <button
            className={`memorySearchSubmit ${searchMode === "ai" ? "ai" : ""}`}
            onClick={() => submitSearch()}
            type="button"
            aria-label={searchMode === "ai" ? "Search memories with AI mode" : "Search memories"}
            title={searchMode === "ai" ? "Search memories with AI mode" : "Search memories"}
          >
            ⌕
          </button>
        </div>
        <div className="gpTopActions">
          <button
            className={`gpActionPill mobileHidden ${showInfoPanel ? "active" : ""}`}
            onClick={() => setShowInfoPanel((value) => !value)}
            type="button"
          >
            Info
          </button>
          <button
            aria-label="Open browse controls"
            className="gpIconButton"
            onClick={() => setShowSidebarPanel((value) => !value)}
            type="button"
          >
            ☰
          </button>
          <button className="gpIconButton" onClick={showRecent} type="button" aria-label="Browse all years">
            ⟳
          </button>
          <div
            aria-live="polite"
            className={`scanStatusBadge compact ${scanTone}`}
            role="status"
            tabIndex={0}
          >
            <ScanStatusIcon status={scanTone} />
            <div className="scanStatusTooltip">
              <p className="scanStatusTooltipTitle">Background scan</p>
              {scanTooltipLines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </div>
          <div
            aria-live="polite"
            className={`scanStatusBadge compact ${aiTone}`}
            role="status"
            tabIndex={0}
          >
            <SparkStatusIcon status={aiTone} />
            <div className="scanStatusTooltip">
              <p className="scanStatusTooltipTitle">AI analysis</p>
              {aiTooltipLines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className={`gpBody memoryBody ${showInfoPanel ? "withInfoPanel" : "withoutInfoPanel"}`}>
        <aside className="memoryRail gpSurface">
          <button
            className={`memoryRailItem ${activePane === "photos" ? "active" : ""}`}
            onClick={() => {
              setActivePane("photos");
              setShowSidebarPanel(false);
            }}
            type="button"
          >
            <span className="memoryRailIcon">▣</span>
            <span className="memoryRailLabel">Timeline</span>
          </button>
          <button
            className={`memoryRailItem ${resultMode === "search" ? "active" : ""}`}
            onClick={() => {
              setActivePane("photos");
              focusSearchInput();
              setShowSidebarPanel(false);
            }}
            type="button"
          >
            <span className="memoryRailIcon">✦</span>
            <span className="memoryRailLabel">Search</span>
          </button>
          <div className="memoryRailDivider" />
          <button
            className={`memoryRailItem secondary ${activePane === "trips" ? "active" : ""}`}
            onClick={() => {
              setActivePane("trips");
              setShowSidebarPanel(false);
            }}
            type="button"
          >
            <span className="memoryRailIcon">☰</span>
            <span className="memoryRailLabel">Trips</span>
          </button>
          <button
            className={`memoryRailItem secondary ${activePane === "map" ? "active" : ""}`}
            onClick={() => {
              setActivePane("map");
              setShowSidebarPanel(false);
            }}
            type="button"
          >
            <span className="memoryRailIcon">⌖</span>
            <span className="memoryRailLabel">Geo Map</span>
          </button>
          <div className="memoryRailStats">
            <div className="memoryRailStatCard">
              <p className="eyebrow">Library</p>
              <strong>{indexedMediaValue}</strong>
              <span>Indexed memories</span>
            </div>
            <div className="memoryRailStatCard">
              <p className="eyebrow">Map</p>
              <strong>{mappedMediaValue}</strong>
              <span>Mapped media</span>
            </div>
            <div className="memoryRailStatCard">
              <p className="eyebrow">Trips</p>
              <strong>{tripRoutesValue}</strong>
              <span>Route groups</span>
            </div>
          </div>
        </aside>

        <section className="gpContent memoryCenter">
          {showSidebarPanel ? (
            <section className="memoryUtilitySheet gpSurface">
              <div className="memoryUtilityHeader">
                <div>
                  <p className="eyebrow">Browse controls</p>
                  <h2>Collections and filters</h2>
                </div>
                <button className="secondaryButton" onClick={() => setShowSidebarPanel(false)} type="button">
                  Close
                </button>
              </div>
              <div className="memoryUtilityGrid">
                <div className="memoryUtilityBlock">
                  <p className="gpSidebarLabel">Collections</p>
                  <button className="gpSidebarLink" onClick={() => setAiCompletedOnly((value) => !value)} type="button">
                    <span>{aiCompletedOnly ? "All indexed media" : "AI-ready photos"}</span>
                    <small>{aiCompletedOnly ? "On" : "Off"}</small>
                  </button>
                  <button className="gpSidebarLink" onClick={() => setGridDensity((value) => value === "comfortable" ? "compact" : "comfortable")} type="button">
                    <span>Grid density</span>
                    <small>{gridDensity === "comfortable" ? "Comfort" : "Day"}</small>
                  </button>
                  <button className="gpSidebarLink" onClick={() => setShowRoutes((value) => !value)} type="button">
                    <span>Trip routes</span>
                    <small>{showRoutes ? "Shown" : "Hidden"}</small>
                  </button>
                  <button className="gpSidebarLink" onClick={() => setActivePane("map")} type="button">
                    <span>Mapped media</span>
                    <small>{mappedMediaValue}</small>
                  </button>
                </div>
                <div className="memoryUtilityBlock">
                  <p className="gpSidebarLabel">Search options</p>
                  <div className="fieldGrid">
                    <label>
                      Media type
                      <select onChange={(event) => setMediaType(event.target.value)} value={mediaType}>
                        <option value="">All media</option>
                        <option value="image">Images</option>
                        <option value="video">Videos</option>
                      </select>
                    </label>
                    <label>
                      Country
                      <input onChange={(event) => setCountry(event.target.value)} placeholder="Scotland" value={country} />
                    </label>
                    <label>
                      City / place
                      <input onChange={(event) => setCity(event.target.value)} placeholder="Glencoe" value={city} />
                    </label>
                    <label>
                      Tag
                      <input onChange={(event) => setTag(event.target.value)} placeholder="hiking" value={tag} />
                    </label>
                  </div>
                </div>
                <div className="memoryUtilityBlock">
                  <p className="gpSidebarLabel">Timeline</p>
                  <div className="timelineBlock">
                    <div className="metaRow">
                      <span>Range</span>
                      <span>
                        {boundedYearStart} to {boundedYearEnd}
                      </span>
                    </div>
                    <input
                      max={timelineMaxYear}
                      min={timelineMinYear}
                      onChange={(event) => setYearStart(Number(event.target.value))}
                      type="range"
                      value={yearStart}
                    />
                    <input
                      max={timelineMaxYear}
                      min={timelineMinYear}
                      onChange={(event) => setYearEnd(Number(event.target.value))}
                      type="range"
                      value={yearEnd}
                    />
                  </div>
                  <p className="helperText">{indexedMediaValue} indexed memories, {mappedMediaValue} mapped, {tripRoutesValue} routes.</p>
                </div>
              </div>
              {resultMode === "search" && searchIsDirty ? (
                <p className="helperText">Filters changed. Press Enter in the top search bar to refresh the ranked results.</p>
              ) : null}
            </section>
          ) : null}
          {surfaceError ? <p className="errorText">{surfaceError}</p> : null}

          {activePane === "map" ? (
            <section className="mapPanel gpSurface">
              <div className="panelHeading">
                <div>
                  <p className="eyebrow">Geo map</p>
                  <h2>Places</h2>
                </div>
                <p>{mapSummary}</p>
              </div>
              <DiscoveryMap
                onOpen={handleOpenMedia}
                onPreview={handleMapPreview}
                onViewportChange={handleViewportChange}
                points={mapResponse.points}
                routes={showRoutes ? mapResponse.routes : []}
                selectedId={selectedId}
                showRoutes={showRoutes}
                zoom={mapZoom}
              />
            </section>
          ) : null}

          {activePane === "trips" ? (
            <section className="resultsPanel gpSurface">
              <div className="panelHeading">
                <div>
                  <p className="eyebrow">Trips</p>
                  <h2>Available trips</h2>
                </div>
                <p>
                  {tripsLoading
                    ? "Loading trips..."
                    : `${COUNT_FORMATTER.format(tripSummaries.length)} trips available with the current filters`}
                </p>
              </div>
              {tripsError ? <p className="errorText">{tripsError}</p> : null}
              {!tripsLoading && !tripSummaries.length && !tripsError ? (
                <div className="vaultPanel">
                  <div className="vaultCard">
                    <h3>No trips matched the current filters</h3>
                    <p>Try broadening the year range or clearing a location or tag filter.</p>
                  </div>
                </div>
              ) : null}
              {tripSummaries.length ? (
                <div className="tripExplorerGrid">
                  {tripSummaries.map((trip) => (
                    <button
                      className={`tripExplorerCard ${activeTripName === trip.trip_name ? "active" : ""}`}
                      key={trip.trip_name}
                      onClick={() => {
                        setActiveTripName(trip.trip_name);
                        setResultMode("browse");
                        setActiveSearchRequest(null);
                        setResultsError(null);
                        setBrowseYear(null);
                        setBrowseMonthKey(null);
                        setActivePane("photos");
                        setShowSidebarPanel(false);
                      }}
                      type="button"
                    >
                      <div className="tripExplorerImageWrap">
                        {trip.cover?.thumbnail_url ? (
                          <img
                            alt={trip.trip_name}
                            className="tripExplorerImage"
                            decoding="async"
                            loading="lazy"
                            src={trip.cover.thumbnail_url}
                          />
                        ) : (
                          <div className="tripExplorerImage tripExplorerPlaceholder" aria-hidden="true">
                            {trip.trip_name.charAt(0)}
                          </div>
                        )}
                        <div className="tripExplorerOverlay" />
                        <span className="tripExplorerCount">{COUNT_FORMATTER.format(trip.count)} photos</span>
                      </div>
                      <div className="tripExplorerContent">
                        <strong>{trip.trip_name}</strong>
                        <span>{formatTripDate(trip.latest_date) ?? "Trip date unavailable"}</span>
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}

          {activePane === "details" ? (
            <section className="resultsPanel gpSurface">
              <div className="panelHeading">
                <div>
                  <p className="eyebrow">Selected memory</p>
                  <h2>{selectedDetail ? selectedDetail.filename : "Details"}</h2>
                </div>
                <div className="detailTopActions">
                  <button className="secondaryButton detailBackButton" onClick={() => setActivePane("photos")} type="button">
                    Back to photos
                  </button>
                  <p>{selectedDetail ? "Inspector" : "Pick a photo"}</p>
                </div>
              </div>
              <InspectorDrawer item={selectedDetail} />
            </section>
          ) : null}

          {activePane === "photos" ? (
            <section className="resultsPanel gpSurface photoStreamPanel">
              <div className="panelHeading">
                <div>
                  <p className="eyebrow">{resultMode === "search" ? "Search results" : "Timeline"}</p>
                  <h2>{resultMode === "search" ? "Photo matches" : resultPanelTitle}</h2>
                </div>
                <div className="photoPanelTools">
                  <div className="viewToggle" role="tablist" aria-label="Photo density">
                    <button
                      className={`viewToggleButton ${gridDensity === "comfortable" ? "active" : ""}`}
                      aria-label="Comfortable view"
                      onClick={() => setGridDensity("comfortable")}
                      title="Comfortable view"
                      type="button"
                    >
                      <span aria-hidden="true" className="viewToggleIcon viewToggleIconComfortable">
                        <span />
                        <span />
                        <span />
                        <span />
                      </span>
                    </button>
                    <button
                      className={`viewToggleButton ${gridDensity === "compact" ? "active" : ""}`}
                      aria-label="Compact view"
                      onClick={() => setGridDensity("compact")}
                      title="Compact view"
                      type="button"
                    >
                      <span aria-hidden="true" className="viewToggleIcon viewToggleIconCompact">
                        <span />
                        <span />
                        <span />
                        <span />
                        <span />
                        <span />
                        <span />
                        <span />
                        <span />
                      </span>
                    </button>
                  </div>
                  <p>{photoSummary}</p>
                </div>
              </div>
              <ResultGrid
                emptyDescription={resultMode === "search"
                  ? "Try a broader search, a different tag, or clear some filters."
                  : selectedBrowseMonthEntry
                    ? "No library photos matched the current filters for this month."
                  : browseYear
                    ? "No library photos matched the current filters for this year."
                    : "No library photos matched the current filters."}
                emptyTitle={resultMode === "search"
                  ? "No ranked matches"
                  : selectedBrowseMonthEntry
                    ? "No photos for this month"
                    : browseYear
                      ? "No photos for this year"
                      : "No photos found"}
                onOpen={handleOpenMedia}
                onFilterTrip={handleFilterTrip}
                onSelect={handleSelectMedia}
                results={results}
                selectedId={selectedId}
                activeTripName={activeTripName}
                density={gridDensity}
                groupByDay={resultMode === "browse" && !!browseYear}
                compactStoryStrip={resultMode === "browse"}
                yearGroups={resultMode === "browse" && !browseYear ? yearGroups : []}
              />
              {resultMode === "browse" && (activeTripName || browseMonthKey) && !resultsError ? (
                <div className="browseLoadMore searchLoadMore">
                  {browseHasMore ? (
                    <button className="secondaryButton" onClick={loadMoreBrowseYear} type="button" disabled={browseLoadingMore}>
                      {browseLoadingMore ? "Loading more photos..." : "Show more"}
                    </button>
                  ) : results.length ? (
                    <span>{browseMonthKey ? "End of month" : "End of trip"}</span>
                  ) : null}
                </div>
              ) : null}
              {resultMode === "browse" && browseYear && !browseMonthKey && !activeTripName && !resultsError ? (
                <div className="browseLoadMore" ref={browseLoadMoreRef}>
                  {browseLoadingMore
                    ? "Loading more photos..."
                    : browseHasMore
                      ? "Scroll for more photos"
                      : results.length
                        ? "End of this year"
                        : ""}
                </div>
              ) : null}
              {resultMode === "search" && !resultsError && searchTotalCount !== null && searchTotalCount > results.length ? (
                <div className="browseLoadMore searchLoadMore">
                  <button className="secondaryButton" onClick={loadMoreSearchResults} type="button" disabled={searchLoadingMore}>
                    {searchLoadingMore ? "Loading more results..." : "Load more"}
                  </button>
                </div>
              ) : null}
            </section>
          ) : null}
        </section>

        {showInfoPanel ? (
          <aside className="memoryInfoPanel gpSurface">
            <div className="panelHeading infoPanelHeader">
              <div>
                <p className="eyebrow">Info panel</p>
                <h2>{selectedDetail ? "Context" : "Select a memory"}</h2>
              </div>
              <button className="secondaryButton" onClick={() => setShowInfoPanel(false)} type="button">
                Hide
              </button>
            </div>
            <InspectorDrawer item={selectedDetail} />
          </aside>
        ) : null}
      </div>

      {resultMode === "browse" ? (
        <div
          className="memoryYearRailShell"
          onBlur={() => clearTimelineHover()}
          onMouseEnter={() => {
            setSecondaryDataEnabled(true);
          }}
          onMouseLeave={() => {
            if (!scrubberDragging) {
              clearTimelineHover();
            }
          }}
        >
          <div className={`memoryYearRail scrubberRail singleMode ${scrubberDragging ? "scrubbing" : ""}`}>
            <div className="memoryScrubberTrack" ref={scrubberTrackRef}>
              <div className="memoryRailTrack" />
              <div
                className="memoryScrubberTouchArea"
                onPointerCancel={stopScrubber}
                onPointerDown={handleScrubberPointerDown}
                onPointerMove={handleScrubberPointerMove}
                onPointerUp={stopScrubber}
              />
              {activeScrubberItem ? (
                <div className="memoryScrubberHandle" style={{ top: `${scrubberHandleProgress * 100}%` }} />
              ) : null}
              {scrubberItems.map((item) => (
                <button
                  className={`memoryYearButton scrubberMarker ${activeScrubberItem?.key === item.key ? "active" : ""}`}
                  key={item.key}
                  onClick={() => applyScrubberSelection(item)}
                  onFocus={() => handleTimelineHover(item.label, item.progress)}
                  onMouseEnter={() => handleTimelineHover(item.label, item.progress)}
                  style={{ top: `${item.progress * 100}%` }}
                  type="button"
                >
                  {item.shortLabel}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {resultMode === "browse" && scrubberItems.length ? (
        <div className="mobileScrubberShell">
          <label className="mobileScrubberField" htmlFor="mobile-scrubber-select">
            <span className="mobileScrubberLabel">Timeline</span>
            <select
              className="mobileScrubberSelect"
              id="mobile-scrubber-select"
              onChange={(event) => {
                setSecondaryDataEnabled(true);
                const nextItem = scrubberItems.find((item) => item.key === event.target.value);
                if (nextItem) {
                  applyScrubberSelection(nextItem);
                }
              }}
              value={activeScrubberItem?.key ?? scrubberItems[0]?.key ?? ""}
            >
              {scrubberItems.map((item) => (
                <option key={`mobile-${item.key}`} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
            <span aria-hidden="true" className="mobileScrubberChevron">▾</span>
          </label>
        </div>
      ) : null}

      <div className="gpMobileDock" role="navigation" aria-label="Mobile navigation">
        <button className={`gpMobileDockButton ${activePane === "photos" ? "active" : ""}`} onClick={() => setActivePane("photos")} type="button">
          <span>▣</span>
          <small>Timeline</small>
        </button>
        <button
          className={`gpMobileDockButton ${resultMode === "search" ? "active" : ""}`}
          onClick={() => {
            setActivePane("photos");
            focusSearchInput();
          }}
          type="button"
        >
          <span>✦</span>
          <small>Search</small>
        </button>
        <button className={`gpMobileDockButton ${activePane === "trips" ? "active" : ""}`} onClick={() => setActivePane("trips")} type="button">
          <span>☰</span>
          <small>Trips</small>
        </button>
        <button className={`gpMobileDockButton ${activePane === "map" ? "active" : ""}`} onClick={() => setActivePane("map")} type="button">
          <span>⌖</span>
          <small>Map</small>
        </button>
      </div>
    </main>
  );
}
