"use client";

import { useEffect, useState } from "react";
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
  const [open, setOpen] = useState(false);

  // A route change is the user having picked something — close the drawer
  // behind them instead of leaving it open over the new page.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  function isActive(href: string, exact?: boolean) {
    if (exact) return pathname === href;
    return pathname === href || pathname.startsWith(href + "/");
  }

  const links = (
    <>
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
    </>
  );

  return (
    <>
      {/* Mobile top bar — the sidebar itself is off-canvas below md, so this
          is the only fixed chrome and the drawer's opener. */}
      <header
        className="md:hidden fixed top-0 inset-x-0 h-14 bg-green-900 flex items-center justify-between px-2 z-30 shadow-md"
        dir="rtl"
      >
        <button
          onClick={() => setOpen(true)}
          aria-label="פתח תפריט"
          aria-expanded={open}
          className="p-2.5 text-white rounded-lg hover:bg-green-700 transition"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <p className="text-sm font-bold text-white">🌿 Smart Order</p>
        <div className="w-9" aria-hidden />
      </header>

      {/* Backdrop, mobile only, only while the drawer is open */}
      {open && (
        <div
          className="md:hidden fixed inset-0 bg-black/40 z-40"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      )}

      {/* The sidebar: an off-canvas drawer below md, sliding in from the
          screen edge it's docked to; a permanent fixed rail at md and up. */}
      <aside
        className={`fixed top-0 right-0 bottom-0 w-64 md:w-56 bg-green-900 flex flex-col z-50 shadow-2xl
          transition-transform duration-200 ease-out
          ${open ? "translate-x-0" : "translate-x-full"} md:translate-x-0`}
        dir="rtl"
      >
        <div className="px-4 py-5 border-b border-green-700 flex items-center justify-between">
          <div>
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
          <button
            onClick={() => setOpen(false)}
            aria-label="סגור תפריט"
            className="md:hidden p-1.5 text-green-300 hover:text-white text-xl leading-none"
          >
            &times;
          </button>
        </div>

        {links}
      </aside>
    </>
  );
}
