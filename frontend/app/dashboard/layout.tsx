"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, logout as apiLogout, Me } from "../lib/api";
import { AppSidebar } from "../components/AppSidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fetchMe()
      .then((data) => { setMe(data); setReady(true); })
      .catch(() => router.push("/login"));
  }, [router]);

  function logout() {
    apiLogout().finally(() => router.push("/login"));
  }

  if (!ready) {
    return (
      <div className="min-h-screen flex items-center justify-center relative z-10">
        <p className="text-white text-lg font-medium opacity-80">טוען...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen relative z-10" dir="rtl">
      <AppSidebar me={me} onLogout={logout} />
      <div className="pt-14 md:pt-0 md:mr-56 min-h-screen">{children}</div>
    </div>
  );
}
