import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geist = Geist({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Smart Order",
  description: "מערכת הזמנות חכמה",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" className={`${geist.className} h-full`}>
      <body className="min-h-full bg-gray-50">{children}</body>
    </html>
  );
}
