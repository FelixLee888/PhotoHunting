"use client";

import dynamic from "next/dynamic";

const LeafletMap = dynamic(() => import("./map/LeafletMap"), {
  ssr: false,
  loading: () => <div className="mapLoading">Loading map…</div>,
});

export { LeafletMap as DiscoveryMap };

