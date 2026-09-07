import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import { ServiceWorkerRegister } from "./components/ServiceWorkerRegister";

const geist = Geist({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Smart Order",
  description: "מערכת הזמנות חכמה",
  // iOS ignores the web manifest for "Add to Home Screen" — this is what
  // makes the installed icon and standalone (no Safari chrome) launch work there.
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Smart Orders",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#14532d",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" className={`${geist.className} h-full`}>
      <body className="min-h-full bg-gray-100">
        <ServiceWorkerRegister />
        {children}
      </body>
    </html>
  );
}
