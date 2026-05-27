"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchProductCatalog, CatalogProduct } from "../../lib/api";

const REGION_LABELS: Record<string, string> = {
  tel_aviv: "תל אביב",
  jerusalem: "ירושלים",
  haifa: "חיפה",
  south: "דרום",
  north: "צפון",
  center: "מרכז",
};

function fmt(n: string | null | undefined) {
  if (!n) return "—";
  return `₪${Number(n).toFixed(2)}`;
}

function MarketBadge({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">
      {label}: {fmt(value)}
    </span>
  );
}

function PriceCell({ suppliers }: { suppliers: CatalogProduct["suppliers"] }) {
  if (suppliers.length === 0)
    return <span className="text-gray-400 text-sm">אין ספק</span>;

  return (
    <div className="flex flex-col gap-1">
      {suppliers.map((s) => (
        <div
          key={s.id}
          className={`flex items-center justify-between gap-3 text-sm px-2 py-1 rounded-lg ${
            s.is_cheapest ? "bg-green-50 font-semibold text-green-800" : "text-gray-600"
          }`}
        >
          <span className="truncate max-w-[120px]" title={s.name}>
            {s.is_cheapest && <span className="ml-1">★</span>}
            {s.name}
            <span className="text-xs text-gray-400 mr-1">
              ({REGION_LABELS[s.region] ?? s.region})
            </span>
          </span>
          <span className="shrink-0">{fmt(s.price)}</span>
        </div>
      ))}
    </div>
  );
}

export default function CatalogPage() {
  const [products, setProducts] = useState<CatalogProduct[] | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    fetchProductCatalog(debouncedSearch || undefined)
      .then(setProducts)
      .catch(() => setError("שגיאה בטעינת הקטלוג"));
  }, [debouncedSearch]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="px-6 py-6">
      {/* Page-level search bar */}
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-xl font-bold text-gray-800 shrink-0">קטלוג מחירים</h1>
        <input
          type="text"
          placeholder="חיפוש מוצר..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-sm border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
      </div>

      {error && <p className="text-red-600 mb-4">{error}</p>}

      {!products ? (
        <p className="text-gray-400 text-center mt-20">טוען...</p>
      ) : products.length === 0 ? (
        <p className="text-gray-400 text-center mt-20">לא נמצאו מוצרים</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs border-b border-gray-100">
                <th className="text-right px-4 py-3 font-medium w-48">מוצר</th>
                <th className="text-right px-4 py-3 font-medium w-20">יחידה</th>
                <th className="text-right px-4 py-3 font-medium w-56">מחיר שוק</th>
                <th className="text-right px-4 py-3 font-medium">ספקים ומחירים</th>
                <th className="text-right px-4 py-3 font-medium w-32">הזול ביותר</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {products.map((p) => (
                <tr key={p.product_id} className="hover:bg-gray-50 transition align-top">
                  <td className="px-4 py-3 font-medium text-gray-800">{p.product_name}</td>
                  <td className="px-4 py-3 text-gray-500">{p.unit_display}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-1">
                      <MarketBadge label='סוג א"' value={p.market_grade_a} />
                      <MarketBadge label="מובחר" value={p.market_premium} />
                      {!p.market_grade_a && !p.market_premium && (
                        <span className="text-gray-400 text-xs">אין נתון</span>
                      )}
                      {p.market_date && (
                        <span className="text-xs text-gray-300">{p.market_date}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <PriceCell suppliers={p.suppliers} />
                  </td>
                  <td className="px-4 py-3">
                    {p.cheapest_price ? (
                      <div>
                        <p className="font-semibold text-green-700">{fmt(p.cheapest_price)}</p>
                        <p className="text-xs text-gray-400">{p.cheapest_supplier_name}</p>
                      </div>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
