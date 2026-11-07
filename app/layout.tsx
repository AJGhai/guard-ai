import type { Metadata, Viewport } from "next";
import type { CSSProperties, ReactNode } from "react";
import { appConfig } from "@/app.config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { brandVariables, pageIconUrl } from "@/lib/app-config";
import "./globals.css";

export const metadata: Metadata = {
  title: appConfig.name,
  description: appConfig.description,
  // app.config.ts icon, or a letter mark in the brand color (no /favicon.ico needed).
  icons: { icon: pageIconUrl(appConfig) },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#111111" },
  ],
};

// Follow the system colour scheme before the first paint, and when it changes.
const THEME_SCRIPT = `(() => {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => document.documentElement.classList.toggle("dark", media.matches);
  apply();
  media.addEventListener("change", apply);
})();`;

// app.config.ts sets the brand colour for buttons, highlights and links.
const brand = brandVariables(appConfig) as CSSProperties;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased" style={brand} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="h-full">
        <TooltipProvider>{children}</TooltipProvider>
      </body>
    </html>
  );
}
