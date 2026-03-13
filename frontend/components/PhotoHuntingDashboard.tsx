"use client";

import { startTransition, useCallback, useDeferredValue, useEffect, useState } from "react";

import { fetchMapPoints, fetchMedia, searchMedia } from "../lib/api";
import type { MapResponse, MediaDetail, SearchRequest, SearchResponse } from "../lib/types";
import { DiscoveryMap } from "./DiscoveryMap";
import { InspectorDrawer } from "./InspectorDrawer";
import { ResultGrid } from "./ResultGrid";

const QUICK_QUERIES = [
  "Show hiking photos in Scotland",
  "Find sunset photos near the sea",
  "Show the videos where we were sailing",
  "Find pictures of my black cat",
];

const START_YEAR = 2000;
const END_YEAR = new Date().getFullYear();

export function PhotoHuntingDashboard() {
  const [query, setQuery] = useState("Show hiking photos in Scotland");
  const deferredQuery = useDeferredValue(query);
  const [mediaType, setMediaType] = useState("");
  const [country, setCountry] = useState("");
  const [city, setCity] = useState("");
  const [tag, setTag] = useState("");
  const [bbox, setBbox] = useState<string | undefined>(undefined);
  const [mapZoom, setMapZoom] = useState(2);
  const [yearStart, setYearStart] = useState(START_YEAR);
  const [yearEnd, setYearEnd] = useState(END_YEAR);
  const [showRoutes, setShowRoutes] = useState(true);
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null);
  const [mapResponse, setMapResponse] = useState<MapResponse>({ points: [], routes: [] });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<MediaDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const boundedYearStart = Math.min(yearStart, yearEnd);
  const boundedYearEnd = Math.max(yearStart, yearEnd);
  const handleViewportChange = useCallback((nextBbox: string, nextZoom: number) => {
    setBbox((current) => (current === nextBbox ? current : nextBbox));
    setMapZoom((current) => (current === nextZoom ? current : nextZoom));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const request: SearchRequest = {
      query: deferredQuery,
      media_type: mediaType || null,
      tags: tag ? [tag] : [],
      country: country || null,
      city: city || null,
      bbox: bbox ?? null,
      date_from: `${boundedYearStart}-01-01`,
      date_to: `${boundedYearEnd}-12-31`,
      limit: 18,
    };

    Promise.all([
      searchMedia(request),
      fetchMapPoints({
        bbox,
        mediaType,
        country,
        city,
        tag,
        dateFrom: `${boundedYearStart}-01-01`,
        dateTo: `${boundedYearEnd}-12-31`,
      }),
    ])
      .then(([searchResult, mapResult]) => {
        if (cancelled) return;
        startTransition(() => {
          setSearchResponse(searchResult);
          setMapResponse(mapResult);
          setSelectedId((current) => {
            if (!current && searchResult.results.length) {
              return searchResult.results[0].id;
            }
            if (current && !searchResult.results.some((result) => result.id === current)) {
              return searchResult.results[0]?.id ?? null;
            }
            return current;
          });
        });
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Unable to load Photo Hunting data.");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [bbox, boundedYearEnd, boundedYearStart, city, country, deferredQuery, mediaType, tag]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedDetail(null);
      return;
    }
    let cancelled = false;
    fetchMedia(selectedId)
      .then((item) => {
        if (!cancelled) {
          setSelectedDetail(item);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSelectedDetail(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  return (
    <main className="pageShell">
      <section className="hero">
        <div>
          <p className="eyebrow">Photo Hunting</p>
          <h1>Search memories by meaning, place, route, and visual context.</h1>
          <p className="heroCopy">
            Multimodal discovery for images, videos, OCR, transcripts, and GPS-aware map browsing.
          </p>
        </div>
        <div className="heroStats">
          <div>
            <span>{searchResponse?.results.length ?? 0}</span>
            <p>ranked matches</p>
          </div>
          <div>
            <span>{mapResponse.points.length}</span>
            <p>mapped media</p>
          </div>
          <div>
            <span>{mapResponse.routes.length}</span>
            <p>trip routes</p>
          </div>
        </div>
      </section>

      <section className="workspace">
        <aside className="controlPanel">
          <div className="panelCard">
            <label className="fieldLabel" htmlFor="query">
              Ask naturally
            </label>
            <textarea
              id="query"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Show photos taken near Glencoe"
              rows={4}
              value={query}
            />
            <div className="quickQueryList">
              {QUICK_QUERIES.map((suggestion) => (
                <button className="chipButton" key={suggestion} onClick={() => setQuery(suggestion)} type="button">
                  {suggestion}
                </button>
              ))}
            </div>
          </div>

          <div className="panelCard">
            <p className="fieldLabel">Filters</p>
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
            <div className="timelineBlock">
              <div className="metaRow">
                <span>Timeline window</span>
                <span>
                  {boundedYearStart} to {boundedYearEnd}
                </span>
              </div>
              <input
                max={END_YEAR}
                min={START_YEAR}
                onChange={(event) => setYearStart(Number(event.target.value))}
                type="range"
                value={yearStart}
              />
              <input
                max={END_YEAR}
                min={START_YEAR}
                onChange={(event) => setYearEnd(Number(event.target.value))}
                type="range"
                value={yearEnd}
              />
            </div>
            <label className="toggleRow">
              <input checked={showRoutes} onChange={() => setShowRoutes((value) => !value)} type="checkbox" />
              Show reconstructed trip routes
            </label>
          </div>

          <div className="panelCard assistantCard">
            <p className="fieldLabel">Search copilot</p>
            <p>{loading ? "Refreshing results…" : searchResponse?.explanation ?? "Ready to search your media."}</p>
            {error ? <p className="errorText">{error}</p> : null}
          </div>
        </aside>

        <section className="mapPanel">
          <div className="panelHeading">
            <div>
              <p className="eyebrow">Geo map</p>
              <h2>Clustered world map</h2>
            </div>
            <p>Bounding box aware, route-ready, and timeline filtered.</p>
          </div>
          <DiscoveryMap
            onSelect={setSelectedId}
            onViewportChange={handleViewportChange}
            points={mapResponse.points}
            routes={showRoutes ? mapResponse.routes : []}
            selectedId={selectedId}
            showRoutes={showRoutes}
            zoom={mapZoom}
          />
        </section>

        <section className="resultsPanel">
          <div className="panelHeading">
            <div>
              <p className="eyebrow">Results</p>
              <h2>Ranked media matches</h2>
            </div>
            <p>{loading ? "Loading…" : `${searchResponse?.results.length ?? 0} cards`}</p>
          </div>
          <ResultGrid onSelect={setSelectedId} results={searchResponse?.results ?? []} selectedId={selectedId} />
          <InspectorDrawer item={selectedDetail} />
        </section>
      </section>
    </main>
  );
}
