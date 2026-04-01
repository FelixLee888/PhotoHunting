import "leaflet/dist/leaflet.css";
import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Photo Hunting",
  description: "Local-first multimodal photo and video search with geo exploration.",
  icons: {
    icon: [{ url: "/photohunting-badge.png", type: "image/png" }],
    shortcut: [{ url: "/photohunting-badge.png", type: "image/png" }],
    apple: [{ url: "/photohunting-badge.png", type: "image/png" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
