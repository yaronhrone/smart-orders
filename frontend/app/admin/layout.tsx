"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, logout as apiLogout, Me } from "../lib/api";
import { AppSidebar } from "../components/AppSidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    fetchMe()
      .then((data) => {
        if (!data.is_staff) {
          router.push("/dashboard");
          return;
        }
        setMe(data);
      })
      .catch(() => router.push("/login"));
  }, [router]);

  function logout() {
    apiLogout().finally(() => router.push("/login"));
  }

  return (
    <div className="min-h-screen bg-gray-50" dir="rtl">
      <AppSidebar me={me} onLogout={logout} />
      <div className="mr-56 min-h-screen">{children}</div>
    </div>
  );
}
