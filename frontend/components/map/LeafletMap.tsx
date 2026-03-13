"use client";

import { useRef } from "react";

import type { LatLngBounds } from "leaflet";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip, useMapEvents } from "react-leaflet";

import type { MapPoint, MapRoute } from "../../lib/types";

type LeafletMapProps = {
  points: MapPoint[];
  routes: MapRoute[];
  selectedId: string | null;
  showRoutes: boolean;
  zoom: number;
  onSelect: (mediaId: string) => void;
  onViewportChange: (bbox: string, zoom: number) => void;
};

function buildClusters(points: MapPoint[], zoom: number) {
  const decimals = zoom <= 3 ? 0 : zoom <= 5 ? 1 : zoom <= 7 ? 2 : zoom <= 10 ? 3 : 4;
  const buckets = new Map<string, { latitude: number; longitude: number; points: MapPoint[] }>();
  for (const point of points) {
    const key = `${point.latitude.toFixed(decimals)}:${point.longitude.toFixed(decimals)}`;
    const existing = buckets.get(key);
    if (existing) {
      existing.points.push(point);
      existing.latitude = (existing.latitude * (existing.points.length - 1) + point.latitude) / existing.points.length;
      existing.longitude = (existing.longitude * (existing.points.length - 1) + point.longitude) / existing.points.length;
    } else {
      buckets.set(key, { latitude: point.latitude, longitude: point.longitude, points: [point] });
    }
  }
  return [...buckets.values()];
}

function boundsToBbox(bounds: LatLngBounds): string {
  const southWest = bounds.getSouthWest();
  const northEast = bounds.getNorthEast();
  return `${southWest.lat},${southWest.lng},${northEast.lat},${northEast.lng}`;
}

function ViewportReporter({ onViewportChange }: { onViewportChange: (bbox: string, zoom: number) => void }) {
  const lastReport = useRef<{ bbox: string | null; zoom: number | null }>({ bbox: null, zoom: null });

  const reportViewport = (mapInstance: ReturnType<typeof useMapEvents>) => {
    const nextBbox = boundsToBbox(mapInstance.getBounds());
    const nextZoom = mapInstance.getZoom();
    if (lastReport.current.bbox === nextBbox && lastReport.current.zoom === nextZoom) {
      return;
    }
    lastReport.current = { bbox: nextBbox, zoom: nextZoom };
    onViewportChange(nextBbox, nextZoom);
  };
  const map = useMapEvents({
    moveend: () => reportViewport(map),
    zoomend: () => reportViewport(map),
  });

  return null;
}

export default function LeafletMap({
  points,
  routes,
  selectedId,
  showRoutes,
  zoom,
  onSelect,
  onViewportChange,
}: LeafletMapProps) {
  const clusters = buildClusters(points, zoom);

  return (
    <MapContainer center={[32, 20]} className="mapCanvas" scrollWheelZoom zoom={2}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <ViewportReporter onViewportChange={onViewportChange} />

      {showRoutes
        ? routes.map((route) => (
            <Polyline
              color="#f0b44c"
              key={route.trip_name}
              opacity={0.75}
              positions={route.coordinates}
              weight={3}
            />
          ))
        : null}

      {clusters.map((cluster, index) => {
        const first = cluster.points[0];
        const isCluster = cluster.points.length > 1;
        const isSelected = !isCluster && selectedId === first.media_id;

        return (
          <CircleMarker
            center={[cluster.latitude, cluster.longitude]}
            eventHandlers={{
              click: (event) => {
                const map = (event.target as { _map?: { flyTo: (center: [number, number], zoomLevel: number) => void; getZoom: () => number } })._map;
                if (isCluster && map) {
                  map.flyTo([cluster.latitude, cluster.longitude], Math.min(map.getZoom() + 2, 12));
                  return;
                }
                onSelect(first.media_id);
              },
            }}
            fillColor={isCluster ? "#f0b44c" : first.media_type === "video" ? "#43b2a7" : "#fc7d5b"}
            fillOpacity={0.9}
            key={`${cluster.latitude}-${cluster.longitude}-${index}`}
            pathOptions={{ color: isSelected ? "#fff7e6" : "#13261f", weight: isSelected ? 3 : 1 }}
            radius={isCluster ? 14 + Math.min(cluster.points.length, 18) : 8}
          >
            <Tooltip direction="top" opacity={1} permanent={isCluster}>
              {isCluster ? `${cluster.points.length}` : first.media_type}
            </Tooltip>
            <Popup>
              <div className="mapPopup">
                {first.thumbnail ? <img alt={first.caption ?? "Preview"} className="mapThumb" src={first.thumbnail} /> : null}
                <p>{first.caption}</p>
                <p>{first.location}</p>
                {isCluster ? <p>{cluster.points.length} media items in this area</p> : null}
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
