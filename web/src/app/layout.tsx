import type { Metadata, Viewport } from "next";
import "@fontsource-variable/instrument-sans/standard.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MindGuard", template: "%s · MindGuard" },
  description: "Your attention, goals, risk and controls in one place.",
  icons: { icon: "/favicon.svg" },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

// Applies the saved theme before paint to avoid a flash; "system" follows prefers-color-scheme.
const themeScript = `try{var t=localStorage.getItem("mg-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
