"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Me } from "../lib/api";

const NAV = [
  { href: "/dashboard", label: "לוח בקרה", exact: true },
  { href: "/dashboard/new-order", label: "הזמנה חדשה" },
  { href: "/dashboard/shopping-lists", label: "רשימות קניות" },
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
      className="w-56 fixed top-0 right-0 bottom-0 bg-white border-l border-gray-200 flex flex-col z-20"
      dir="rtl"
    >
      <div className="px-4 py-5 border-b border-gray-100">
        <p className="text-base font-bold text-gray-900">Smart Orders</p>
        {me && (
          <p className="text-xs text-gray-500 mt-0.5 truncate">
            {me.first_name} {me.last_name}
          </p>
        )}
      </div>

      <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-0.5">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center px-3 py-2 rounded-lg text-sm transition ${
              isActive(item.href, item.exact)
                ? "bg-blue-50 text-blue-700 font-semibold"
                : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            }`}
          >
            {item.label}
          </Link>
        ))}

        {me?.is_staff && (
          <>
            <p className="text-xs text-gray-400 px-3 pt-4 pb-1 font-medium">ניהול</p>
            {ADMIN_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center px-3 py-2 rounded-lg text-sm transition ${
                  isActive(item.href, item.exact)
                    ? "bg-gray-800 text-white font-semibold"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </>
        )}
      </nav>

      <div className="px-3 py-3 border-t border-gray-100">
        <button
          onClick={onLogout}
          className="w-full text-right px-3 py-2 text-sm text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition"
        >
          יציאה
        </button>
      </div>
    </aside>
  );
}
