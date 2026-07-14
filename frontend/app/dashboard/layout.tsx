"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, Me } from "../lib/api";
import { AppSidebar } from "../components/AppSidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    fetchMe()
      .then((data) => { setMe(data); setReady(true); })
      .catch(() => {
        localStorage.removeItem("token");
        router.push("/login");
      });
  }, [router]);

  function logout() {
    localStorage.removeItem("token");
    router.push("/login");
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
      <div className="mr-56 min-h-screen">{children}</div>
    </div>
  );
}
