"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, Me } from "../lib/api";
import { AppSidebar } from "../components/AppSidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    fetchMe()
      .then((data) => {
        if (!data.is_staff) {
          router.push("/dashboard");
          return;
        }
        setMe(data);
      })
      .catch(() => {
        localStorage.removeItem("token");
        router.push("/login");
      });
  }, [router]);

  function logout() {
    localStorage.removeItem("token");
    router.push("/login");
  }

  return (
    <div className="min-h-screen bg-gray-50" dir="rtl">
      <AppSidebar me={me} onLogout={logout} />
      <div className="mr-56 min-h-screen">{children}</div>
    </div>
  );
}
