"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Me } from "../lib/api";


const NAV = [
  { href: "/dashboard", label: "לוח בקרה", exact: true },
  { href: "/dashboard/new-order", label: "הזמנה חדשה" },
  { href: "/dashboard/catalog", label: "קטלוג מחירים" },
];

const ADMIN_NAV = [
  { href: "/admin", label: "לקוחות", exact: true },
  { href: "/admin/suppliers", label: "ספקים" },
  { href: "/admin/catalog", label: "קטלוג מוצרים" },
];

export function AppSidebar({ me, onLogout }: { me: Me | null; onLogout: () => void }) {
  const pathname = usePathname();

  function isActive(href: string, exact?: boolean) {
    if (exact) return pathname === href;
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <aside
      className="w-56 fixed top-0 right-0 bottom-0 bg-green-900 flex flex-col z-20 shadow-2xl"
      dir="rtl"
    >
      <div className="px-4 py-5 border-b border-green-700">
        <p className="text-base font-bold text-white">🌿 Smart Order</p>
        {me && (
          <Link
            href="/dashboard/profile"
            className="block text-xs text-green-300 mt-0.5 truncate hover:text-white transition"
            title="פרופיל חברה"
          >
            {me.first_name} {me.last_name}
          </Link>
        )}
      </div>

      <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-0.5">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center px-3 py-2 rounded-lg text-sm transition ${
              isActive(item.href, item.exact)
                ? "bg-white text-green-900 font-semibold"
                : "text-green-100 hover:bg-green-700 hover:text-white"
            }`}
          >
            {item.label}
          </Link>
        ))}

        {me?.is_staff && (
          <>
            <p className="text-xs text-green-400 px-3 pt-4 pb-1 font-medium">ניהול</p>
            {ADMIN_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center px-3 py-2 rounded-lg text-sm transition ${
                  isActive(item.href, item.exact)
                    ? "bg-white text-green-900 font-semibold"
                    : "text-green-100 hover:bg-green-700 hover:text-white"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </>
        )}
      </nav>

      <div className="px-3 py-3 border-t border-green-700">
        <button
          onClick={onLogout}
          className="w-full text-right px-3 py-2 text-sm text-green-300 hover:text-white hover:bg-green-700 rounded-lg transition"
        >
          יציאה
        </button>
      </div>
    </aside>
  );
}
