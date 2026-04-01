"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchTVHome, fetchTVPlaylist, mediaPreviewUrl, mediaStreamUrl } from "../lib/api";
import type { TVHomeResponse, TVPlaylistResponse } from "../lib/types";

const HOME_TRIP_LIMIT = 12;
const PLAYLIST_LIMIT = 120;
const ALBUM_BROWSER_LIMIT = 20;
const ALBUM_BROWSER_COLUMNS = 5;
const SLIDESHOW_INTERVAL_MS = 7000;
const TV_VIEWER_IDLE_MS = 3000;
const TV_BACKGROUND_TRACKS = [
  "A Town With an Ocean View.mp3",
  "All_Ways_Moving_(getmp3.pro).mp3",
  "A Year Ago (Instrumental) - NEFFEX.mp3",
  "Alone Time - Slynk.mp3",
  "Gemini - half.cool.mp3",
  "KEEM.THE.CIPHER.mp3",
  "Mama Makzo Polaroid.mp3",
  "Ouska - Alive.mp3",
  "Pabzzz Together BChillhop Release.mp3",
  "While_My_Guitar_Gently_Weeps_The_B_(getmp3.pro).mp3",
  "_Wendy_Wander_-_Officia_(getmp3.pro).mp3",
  "海が見える街.mp3",
].map((filename) => `/mp3/${encodeURIComponent(filename)}`);

function pickRandomTrack(previousTrack?: string | null): string | null {
  if (!TV_BACKGROUND_TRACKS.length) {
    return null;
  }
  const pool = TV_BACKGROUND_TRACKS.length > 1 && previousTrack
    ? TV_BACKGROUND_TRACKS.filter((track) => track !== previousTrack)
    : TV_BACKGROUND_TRACKS;
  return pool[Math.floor(Math.random() * pool.length)] ?? TV_BACKGROUND_TRACKS[0] ?? null;
}

function formatDateLabel(value?: string | null): string {
  if (!value) {
    return "Recently added";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Recently added";
  }
  return parsed.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function getRandomSlideIndex(total: number, current: number): number {
  if (total <= 1) {
    return 0;
  }
  let nextIndex = current;
  while (nextIndex === current) {
    nextIndex = Math.floor(Math.random() * total);
  }
  return nextIndex;
}

export function TVMode() {
  const [home, setHome] = useState<TVHomeResponse | null>(null);
  const [homeLoading, setHomeLoading] = useState(true);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [homeFocusTarget, setHomeFocusTarget] = useState<"grid" | "prev" | "next">("grid");
  const [tripOffset, setTripOffset] = useState(0);
  const [activePlaylist, setActivePlaylist] = useState<TVPlaylistResponse | null>(null);
  const [activeSlideIndex, setActiveSlideIndex] = useState(0);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const [autoPlay, setAutoPlay] = useState(true);
  const [randomPlayback, setRandomPlayback] = useState(false);
  const [viewerChromeVisible, setViewerChromeVisible] = useState(true);
  const [viewerSourceMode, setViewerSourceMode] = useState<"tv" | "default" | "stream">("tv");
  const [albumBrowser, setAlbumBrowser] = useState<TVPlaylistResponse | null>(null);
  const [albumLoading, setAlbumLoading] = useState(false);
  const [albumLoadingMore, setAlbumLoadingMore] = useState(false);
  const [albumError, setAlbumError] = useState<string | null>(null);
  const [albumFocusTarget, setAlbumFocusTarget] = useState<"grid" | "back" | "more">("grid");
  const [albumFocusIndex, setAlbumFocusIndex] = useState(0);
  const [albumOffset, setAlbumOffset] = useState(0);
  const playlistCacheRef = useRef<Map<string, TVPlaylistResponse>>(new Map());
  const backgroundAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentBackgroundTrackRef = useRef<string | null>(null);
  const viewerIdleTimeoutRef = useRef<number | null>(null);

  const clearViewerIdleTimeout = useCallback(() => {
    if (viewerIdleTimeoutRef.current !== null) {
      window.clearTimeout(viewerIdleTimeoutRef.current);
      viewerIdleTimeoutRef.current = null;
    }
  }, []);

  const scheduleViewerChromeHide = useCallback(() => {
    clearViewerIdleTimeout();
    viewerIdleTimeoutRef.current = window.setTimeout(() => {
      setViewerChromeVisible(false);
    }, TV_VIEWER_IDLE_MS);
  }, [clearViewerIdleTimeout]);

  const showViewerChrome = useCallback(() => {
    setViewerChromeVisible(true);
    if (activePlaylist && !albumBrowser) {
      scheduleViewerChromeHide();
    }
  }, [activePlaylist, albumBrowser, scheduleViewerChromeHide]);

  const loadHome = useCallback(async (offset: number, signal?: AbortSignal) => {
    setHomeLoading(true);
    setHomeError(null);
    try {
      const response = await fetchTVHome(
        {
          recentLimit: 12,
          tripLimit: HOME_TRIP_LIMIT,
          tripOffset: offset,
          analysisStatus: "completed",
        },
        signal,
      );
      setHome(response);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setHomeError(error instanceof Error ? error.message : "Unable to load TV mode.");
      }
    } finally {
      setHomeLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadHome(tripOffset, controller.signal);
    return () => {
      controller.abort();
    };
  }, [loadHome, tripOffset]);

  const tripItems = home?.recent_trips ?? [];
  const photoItems = home?.recent_photos ?? [];
  const tripHasPrevious = tripOffset > 0;
  const tripHasMore = home?.trips_has_more ?? false;
  const tripRangeStart = tripItems.length ? tripOffset + 1 : 0;
  const tripRangeEnd = tripOffset + tripItems.length;

  const loadNextTripPage = useCallback(() => {
    if (homeLoading || !tripHasMore || !tripItems.length) {
      return;
    }
    setTripOffset((current) => current + tripItems.length);
    setFocusIndex(0);
    setHomeFocusTarget("grid");
  }, [homeLoading, tripHasMore, tripItems.length]);

  const loadPreviousTripPage = useCallback(() => {
    if (homeLoading || !tripHasPrevious) {
      return;
    }
    setTripOffset((current) => Math.max(current - HOME_TRIP_LIMIT, 0));
    setFocusIndex(0);
    setHomeFocusTarget("grid");
  }, [homeLoading, tripHasPrevious]);

  useEffect(() => {
    if (!tripItems.length) {
      setFocusIndex(0);
      setHomeFocusTarget("grid");
      return;
    }
    if (focusIndex > tripItems.length - 1) {
      setFocusIndex(tripItems.length - 1);
    }
  }, [focusIndex, tripItems]);

  const focusedTrip = tripItems[focusIndex] ?? tripItems[0] ?? null;
  const heroPhoto = focusedTrip?.cover ?? photoItems[0] ?? tripItems[0]?.cover ?? null;

  const openPlaylist = useCallback(
    async (kind: "recent" | "trip", startIndex: number, tripName?: string) => {
      const cacheKey = kind === "trip" && tripName ? `trip:${tripName}` : "recent";
      const cached = playlistCacheRef.current.get(cacheKey);
      setViewerError(null);
      setViewerLoading(true);
      setAutoPlay(true);
      setRandomPlayback(false);
      setViewerChromeVisible(true);

      if (cached) {
        setActivePlaylist(cached);
        setActiveSlideIndex(Math.min(startIndex, Math.max(cached.items.length - 1, 0)));
        setViewerLoading(false);
        return;
      }

      try {
        const playlist = await fetchTVPlaylist({
          kind,
          tripName,
          limit: PLAYLIST_LIMIT,
          analysisStatus: "completed",
        });
        playlistCacheRef.current.set(cacheKey, playlist);
        setActivePlaylist(playlist);
        setActiveSlideIndex(Math.min(startIndex, Math.max(playlist.items.length - 1, 0)));
      } catch (error) {
        setViewerError(error instanceof Error ? error.message : "Unable to open playlist.");
      } finally {
        setViewerLoading(false);
      }
    },
    [],
  );

  const closeViewer = useCallback(() => {
    setActivePlaylist(null);
    setActiveSlideIndex(0);
    setViewerError(null);
    setRandomPlayback(false);
    setViewerChromeVisible(true);
    setViewerSourceMode("tv");
    setAlbumBrowser(null);
    setAlbumError(null);
    setAlbumLoading(false);
    setAlbumLoadingMore(false);
    setAlbumFocusIndex(0);
    setAlbumFocusTarget("grid");
    setAlbumOffset(0);
    setViewerChromeVisible(true);
  }, []);

  const closeAlbumBrowser = useCallback(() => {
    setAlbumBrowser(null);
    setAlbumError(null);
    setAlbumLoading(false);
    setAlbumLoadingMore(false);
    setAlbumFocusIndex(0);
    setAlbumFocusTarget("grid");
    setAlbumOffset(0);
  }, []);

  const advanceSlide = useCallback((direction: -1 | 1) => {
    setActiveSlideIndex((current) => {
      if (!activePlaylist?.items.length) {
        return current;
      }
      const nextIndex = current + direction;
      if (nextIndex < 0) {
        return activePlaylist.items.length - 1;
      }
      if (nextIndex >= activePlaylist.items.length) {
        return 0;
      }
      return nextIndex;
    });
  }, [activePlaylist]);

  const jumpToRandomSlide = useCallback(() => {
    setActiveSlideIndex((current) => {
      if (!activePlaylist?.items.length) {
        return current;
      }
      return getRandomSlideIndex(activePlaylist.items.length, current);
    });
  }, [activePlaylist]);

  useEffect(() => {
    if (!albumBrowser?.items.length || albumFocusTarget !== "grid") {
      return;
    }
    const target = document.getElementById(`tv-album-thumb-${albumFocusIndex}`);
    target?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [albumBrowser, albumFocusIndex, albumFocusTarget]);

  useEffect(() => {
    if (!activePlaylist?.items.length || !autoPlay) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setActiveSlideIndex((current) => {
        if (!activePlaylist.items.length) {
          return current;
        }
        if (randomPlayback) {
          return getRandomSlideIndex(activePlaylist.items.length, current);
        }
        return (current + 1) % activePlaylist.items.length;
      });
    }, SLIDESHOW_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activePlaylist, autoPlay, randomPlayback]);

  useEffect(() => {
    if (!activePlaylist?.items.length) {
      return undefined;
    }
    setViewerSourceMode("tv");
    return undefined;
  }, [activePlaylist, activeSlideIndex]);

  useEffect(() => {
    if (!activePlaylist || albumBrowser) {
      clearViewerIdleTimeout();
      setViewerChromeVisible(true);
      return undefined;
    }
    setViewerChromeVisible(true);
    scheduleViewerChromeHide();
    return () => {
      clearViewerIdleTimeout();
    };
  }, [activePlaylist, albumBrowser, clearViewerIdleTimeout, scheduleViewerChromeHide]);

  useEffect(() => {
    const audio = backgroundAudioRef.current ?? new Audio();
    backgroundAudioRef.current = audio;
    audio.preload = "auto";
    audio.loop = false;
    audio.volume = 0.18;

    if (!activePlaylist) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      currentBackgroundTrackRef.current = null;
      return undefined;
    }

    let cancelled = false;

    const playRandomTrack = async () => {
      const nextTrack = pickRandomTrack(currentBackgroundTrackRef.current);
      if (!nextTrack || cancelled) {
        return;
      }
      currentBackgroundTrackRef.current = nextTrack;
      audio.src = nextTrack;
      audio.currentTime = 0;
      try {
        await audio.play();
      } catch {
        // TV browsers can reject autoplay sporadically.
      }
    };

    const handleEnded = () => {
      void playRandomTrack();
    };

    audio.addEventListener("ended", handleEnded);
    void playRandomTrack();

    return () => {
      cancelled = true;
      audio.removeEventListener("ended", handleEnded);
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      currentBackgroundTrackRef.current = null;
    };
  }, [activePlaylist?.playlist_id]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (albumBrowser) {
        const hasMore = albumOffset + albumBrowser.items.length < albumBrowser.total_count;
        if (event.key === "Escape" || event.key === "Backspace") {
          event.preventDefault();
          closeAlbumBrowser();
          return;
        }
        if (albumFocusTarget === "grid") {
          if (event.key === "ArrowRight") {
            event.preventDefault();
            setAlbumFocusIndex((current) => Math.min(current + 1, albumBrowser.items.length - 1));
            return;
          }
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            setAlbumFocusIndex((current) => Math.max(current - 1, 0));
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setAlbumFocusIndex((current) => Math.min(current + ALBUM_BROWSER_COLUMNS, albumBrowser.items.length - 1));
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            if (albumFocusIndex >= ALBUM_BROWSER_COLUMNS) {
              setAlbumFocusIndex((current) => Math.max(current - ALBUM_BROWSER_COLUMNS, 0));
            } else {
              setAlbumFocusTarget("back");
            }
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            const selected = albumBrowser.items[albumFocusIndex];
            if (!selected) {
              return;
            }
            const selectedIndex = activePlaylist?.items.findIndex((item) => item.id === selected.id) ?? -1;
            if (selectedIndex >= 0) {
              setActiveSlideIndex(selectedIndex);
            } else if (activePlaylist) {
              setActivePlaylist({
                ...activePlaylist,
                items: albumBrowser.items,
                total_count: albumBrowser.total_count,
              });
              setActiveSlideIndex(albumFocusIndex);
            }
            setAutoPlay(false);
            closeAlbumBrowser();
            return;
          }
          return;
        }

        if (event.key === "ArrowDown") {
          event.preventDefault();
          if (albumBrowser.items.length) {
            setAlbumFocusTarget("grid");
          }
          return;
        }
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          if (hasMore) {
            setAlbumFocusTarget((current) => (current === "back" ? "more" : "back"));
          }
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (albumFocusTarget === "back") {
            closeAlbumBrowser();
            return;
          }
          if (albumFocusTarget === "more") {
            if (!hasMore || albumLoadingMore) {
              return;
            }
            void (async () => {
              setAlbumLoadingMore(true);
              setAlbumError(null);
              try {
                const nextPage = await fetchTVPlaylist({
                  kind: "trip",
                  tripName: albumBrowser.title,
                  limit: ALBUM_BROWSER_LIMIT,
                  offset: albumOffset + albumBrowser.items.length,
                  analysisStatus: "completed",
                });
                setAlbumBrowser(nextPage);
                setAlbumOffset(albumOffset + albumBrowser.items.length);
                setAlbumFocusIndex(0);
                setAlbumFocusTarget("grid");
              } catch (error) {
                setAlbumError(error instanceof Error ? error.message : "Unable to load more album photos.");
              } finally {
                setAlbumLoadingMore(false);
              }
            })();
          }
          return;
        }
        return;
      }

      if (activePlaylist) {
        showViewerChrome();
        const backgroundAudio = backgroundAudioRef.current;
        if (backgroundAudio?.paused) {
          void backgroundAudio.play().catch(() => undefined);
        }
        if (event.key === "ArrowRight") {
          event.preventDefault();
          advanceSlide(1);
          return;
        }
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          advanceSlide(-1);
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setAutoPlay((current) => !current);
          return;
        }
        if (event.key === "Escape" || event.key === "Backspace") {
          event.preventDefault();
          closeViewer();
        }
        return;
      }

      if (!tripItems.length) {
        return;
      }
      if (homeFocusTarget === "prev") {
        if (event.key === "ArrowRight") {
          event.preventDefault();
          setHomeFocusTarget("grid");
          setFocusIndex(0);
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          loadPreviousTripPage();
          return;
        }
        return;
      }
      if (homeFocusTarget === "next") {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          setHomeFocusTarget("grid");
          setFocusIndex(Math.max(tripItems.length - 1, 0));
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          loadNextTripPage();
          return;
        }
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        if (focusIndex >= tripItems.length - 1) {
          if (tripHasMore) {
            setHomeFocusTarget("next");
          }
          return;
        }
        setFocusIndex((current) => Math.min(current + 1, tripItems.length - 1));
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (focusIndex <= 0) {
          if (tripHasPrevious) {
            setHomeFocusTarget("prev");
          }
          return;
        }
        setFocusIndex((current) => Math.max(current - 1, 0));
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        const trip = tripItems[focusIndex];
        if (trip) {
          void openPlaylist("trip", 0, trip.trip_name);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    activePlaylist,
    advanceSlide,
    albumBrowser,
    albumOffset,
    albumFocusIndex,
    albumFocusTarget,
    albumLoadingMore,
    closeAlbumBrowser,
    closeViewer,
    focusIndex,
    homeFocusTarget,
    loadNextTripPage,
    loadPreviousTripPage,
    openPlaylist,
    showViewerChrome,
    tripHasMore,
    tripHasPrevious,
    tripItems,
  ]);

  const activeSlide = useMemo(() => {
    if (!activePlaylist?.items.length) {
      return null;
    }
    return activePlaylist.items[activeSlideIndex] ?? activePlaylist.items[0] ?? null;
  }, [activePlaylist, activeSlideIndex]);
  const activeSlideSrc = activeSlide
    ? viewerSourceMode === "tv"
      ? mediaPreviewUrl(activeSlide.id, "tv")
      : viewerSourceMode === "default"
        ? activeSlide.thumbnail_url || mediaPreviewUrl(activeSlide.id)
        : mediaStreamUrl(activeSlide.id)
    : null;

  const tripAlbumAvailable = Boolean(activePlaylist?.playlist_id.startsWith("trip:"));
  const albumHasMore = Boolean(albumBrowser && albumOffset + albumBrowser.items.length < albumBrowser.total_count);
  const albumRangeStart = albumBrowser ? albumOffset + 1 : 0;
  const albumRangeEnd = albumBrowser ? albumOffset + albumBrowser.items.length : 0;

  return (
    <main className="tvModeShell">
      <div className="tvBackdrop" aria-hidden="true">
        {heroPhoto?.thumbnail_url ? <img alt="" className="tvBackdropImage" src={heroPhoto.thumbnail_url} /> : null}
      </div>

      {!activePlaylist ? (
        <section className="tvTopBar">
          <a className="tvBrand" href="/">
            <img
              alt="Photo Hunting logo"
              className="tvBrandLogo"
              decoding="async"
              height={56}
              src="/photohunting-badge.png"
              width={56}
            />
            <div>
              <p className="tvEyebrow">Photo Hunting</p>
              <h1>TV Mode</h1>
            </div>
          </a>
          <button
            className="tvPrimaryButton tvTopAction"
            disabled={!tripItems.length}
            onClick={() => {
              if (tripItems[0]) {
                void openPlaylist("trip", 0, tripItems[0].trip_name);
              }
            }}
            type="button"
          >
            Play latest trip
          </button>
        </section>
      ) : null}

      {activePlaylist ? (
        <section className="tvViewer">
          {viewerError ? <p className="tvError">{viewerError}</p> : null}
          {activeSlide ? (
            <>
              <div
                className={`tvViewerStage ${viewerChromeVisible || albumBrowser ? "" : "chromeHidden"}`}
                onMouseMove={() => {
                  if (!albumBrowser) {
                    showViewerChrome();
                  }
                }}
              >
                <button className={`tvViewerClose ${viewerChromeVisible || albumBrowser ? "" : "hidden"}`} onClick={closeViewer} type="button">
                  Back to TV home
                </button>
                <img
                  alt={activeSlide.caption || activeSlide.filename}
                  className="tvViewerImage"
                  onError={() => {
                    setViewerSourceMode((current) => {
                      if (current === "tv") {
                        return "default";
                      }
                      if (current === "default") {
                        return "stream";
                      }
                      return "stream";
                    });
                  }}
                  src={activeSlideSrc ?? undefined}
                />
                {albumBrowser ? (
                  <div className="tvAlbumBrowser" role="dialog" aria-label={`${albumBrowser.title} photos`}>
                    <div className="tvAlbumBrowserHeader">
                      <div>
                        <p className="tvEyebrow">Album browser</p>
                        <h2>{albumBrowser.title}</h2>
                        <p>
                          Showing {albumRangeStart}-{albumRangeEnd} of {albumBrowser.total_count} photos
                        </p>
                      </div>
                      <div className="tvAlbumBrowserActions">
                        <button
                          className={`tvNavButton ${albumFocusTarget === "back" ? "focused" : ""}`}
                          onClick={closeAlbumBrowser}
                          type="button"
                        >
                          Back to slideshow
                        </button>
                        {albumHasMore ? (
                          <button
                            className={`tvPrimaryButton ${albumFocusTarget === "more" ? "focused" : ""}`}
                            disabled={albumLoadingMore}
                            onClick={async () => {
                              if (!albumBrowser || albumLoadingMore) {
                                return;
                              }
                              setAlbumLoadingMore(true);
                              setAlbumError(null);
                              try {
                                const nextPage = await fetchTVPlaylist({
                                  kind: "trip",
                                  tripName: albumBrowser.title,
                                  limit: ALBUM_BROWSER_LIMIT,
                                  offset: albumOffset + albumBrowser.items.length,
                                  analysisStatus: "completed",
                                });
                                setAlbumBrowser(nextPage);
                                setAlbumOffset(albumOffset + albumBrowser.items.length);
                                setAlbumFocusIndex(0);
                                setAlbumFocusTarget("grid");
                              } catch (error) {
                                setAlbumError(error instanceof Error ? error.message : "Unable to load more album photos.");
                              } finally {
                                setAlbumLoadingMore(false);
                              }
                            }}
                            type="button"
                          >
                            {albumLoadingMore ? "Loading…" : "Show next 20"}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {albumError ? <p className="tvError tvAlbumBrowserError">{albumError}</p> : null}
                    <div className="tvAlbumBrowserGrid">
                      {albumBrowser.items.map((item, index) => (
                        <button
                          className={`tvAlbumThumb ${albumFocusTarget === "grid" && albumFocusIndex === index ? "focused" : ""}`}
                          id={`tv-album-thumb-${index}`}
                          key={item.id}
                          onClick={() => {
                            const selectedIndex = activePlaylist.items.findIndex((current) => current.id === item.id);
                            if (selectedIndex >= 0) {
                              setActiveSlideIndex(selectedIndex);
                            } else {
                              setActivePlaylist({
                                ...activePlaylist,
                                items: albumBrowser.items,
                                total_count: albumBrowser.total_count,
                              });
                              setActiveSlideIndex(index);
                            }
                            setAutoPlay(false);
                            closeAlbumBrowser();
                          }}
                          type="button"
                        >
                          {item.thumbnail_url ? (
                            <img alt={item.caption || item.filename} className="tvAlbumThumbImage" src={item.thumbnail_url} />
                          ) : (
                            <div className="tvAlbumThumbImage tvTripPlaceholder" aria-hidden="true">
                              {item.filename.charAt(0)}
                            </div>
                          )}
                        </button>
                      ))}
                    </div>
                    {albumLoading ? (
                      <div className="tvLoadingState tvAlbumBrowserLoading">
                        <p className="tvEyebrow">Loading album</p>
                        <h2>Preparing all photos…</h2>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <>
                    <div className={`tvViewerOverlay ${viewerChromeVisible ? "" : "hidden"}`}>
                      <div>
                        <p className="tvEyebrow">{activePlaylist.title}</p>
                        <h2>{activeSlide.trip_name || activeSlide.caption || activeSlide.filename}</h2>
                        <p>{activePlaylist.subtitle || formatDateLabel(activeSlide.date_taken)}</p>
                      </div>
                      <div className="tvViewerStatus">
                        <span>
                          {activeSlideIndex + 1} / {activePlaylist.total_count}
                        </span>
                        <button className={`tvAutoPlayButton ${autoPlay ? "active" : ""}`} onClick={() => setAutoPlay((current) => !current)} type="button">
                          {autoPlay ? "Pause" : "Play"}
                        </button>
                        <button
                          className={`tvAutoPlayButton ${randomPlayback ? "active" : ""}`}
                          onClick={() => {
                            setRandomPlayback((current) => {
                              const nextValue = !current;
                              if (nextValue && activePlaylist?.items.length) {
                                setActiveSlideIndex((slideIndex) => getRandomSlideIndex(activePlaylist.items.length, slideIndex));
                              }
                              return nextValue;
                            });
                          }}
                          type="button"
                        >
                          {randomPlayback ? "Random on" : "Random"}
                        </button>
                      </div>
                    </div>
                    <div className={`tvViewerControls ${viewerChromeVisible ? "" : "hidden"}`}>
                      <button className="tvNavButton" onClick={() => advanceSlide(-1)} type="button">
                        Previous
                      </button>
                      <button className="tvNavButton" onClick={() => advanceSlide(1)} type="button">
                        Next
                      </button>
                      {tripAlbumAvailable ? (
                        <button
                          className="tvNavButton"
                          onClick={async () => {
                            if (!activePlaylist?.playlist_id.startsWith("trip:")) {
                              return;
                            }
                            setAlbumLoading(true);
                            setAlbumError(null);
                            setAutoPlay(false);
                            try {
                              const album = await fetchTVPlaylist({
                                kind: "trip",
                                tripName: activePlaylist.title,
                                limit: ALBUM_BROWSER_LIMIT,
                                offset: 0,
                                analysisStatus: "completed",
                              });
                              setAlbumBrowser(album);
                              setAlbumOffset(0);
                              setAlbumFocusIndex(Math.min(activeSlideIndex, Math.max(album.items.length - 1, 0)));
                              setAlbumFocusTarget("grid");
                            } catch (error) {
                              setAlbumError(error instanceof Error ? error.message : "Unable to open album browser.");
                            } finally {
                              setAlbumLoading(false);
                            }
                          }}
                          type="button"
                        >
                          View album photos
                        </button>
                      ) : null}
                    </div>
                  </>
                )}
              </div>
            </>
          ) : viewerLoading ? (
            <div className="tvLoadingState">
              <p className="tvEyebrow">Loading playlist</p>
              <h2>Preparing your slideshow…</h2>
            </div>
          ) : null}
        </section>
      ) : (
        <section className="tvHome">
          {homeError ? <p className="tvError">{homeError}</p> : null}

          <section className="tvRowSection">
            <div className="tvRowHeader">
              <div>
                <p className="tvEyebrow">Trips</p>
                <h3>All trips</h3>
              </div>
              <p>
                {tripItems.length
                  ? `Showing ${tripRangeStart}-${tripRangeEnd} trips`
                  : "No trips ready yet"}
              </p>
            </div>
            <div className="tvCardRow">
              {homeLoading ? (
                Array.from({ length: HOME_TRIP_LIMIT }, (_, index) => (
                  <div className="tvCardSkeleton trip" key={`trip-skeleton-${index}`} />
                ))
              ) : (
                tripItems.map((trip, index) => (
                  <button
                    className={`tvTripCard ${focusIndex === index ? "focused" : ""}`}
                    key={trip.trip_name}
                    onClick={() => {
                      setFocusIndex(index);
                      void openPlaylist("trip", 0, trip.trip_name);
                    }}
                    type="button"
                  >
                    {trip.cover?.thumbnail_url ? (
                      <img alt={trip.trip_name} className="tvTripImage" src={trip.cover.thumbnail_url} />
                    ) : (
                      <div className="tvTripImage tvTripPlaceholder" aria-hidden="true">
                        {trip.trip_name.charAt(0)}
                      </div>
                    )}
                    <div className="tvTripOverlay" />
                    <div className="tvTripMeta">
                      <strong>{trip.trip_name}</strong>
                      <span>{trip.count.toLocaleString()} photos</span>
                    </div>
                  </button>
                ))
              )}
            </div>
            {tripHasPrevious ? (
              <button
                aria-label="Previous 12 trips"
                className={`tvTripsPagerButton tvTripsPagerButtonPrev ${homeFocusTarget === "prev" ? "focused" : ""}`}
                disabled={homeLoading}
                onClick={loadPreviousTripPage}
                type="button"
              >
                <span aria-hidden="true">‹</span>
              </button>
            ) : null}
            {tripHasMore ? (
              <button
                aria-label="Show next 12 trips"
                className={`tvTripsPagerButton tvTripsPagerButtonNext ${homeFocusTarget === "next" ? "focused" : ""}`}
                disabled={homeLoading}
                onClick={loadNextTripPage}
                type="button"
              >
                <span aria-hidden="true">›</span>
              </button>
            ) : null}
          </section>
        </section>
      )}
    </main>
  );
}
