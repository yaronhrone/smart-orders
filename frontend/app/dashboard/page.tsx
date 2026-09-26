"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchOrderBatches, fetchStats, OrderBatchSummary, OrderStats } from "../lib/api";
import { StatusBadge, deliveredSummary, formatCurrency, formatDateTime } from "../lib/orderStatus";

const ORDERS_PAGE_SIZE = 10;

export default function DashboardPage() {
  const router = useRouter();
  const [batches, setBatches] = useState<OrderBatchSummary[] | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [stats, setStats] = useState<OrderStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([fetchOrderBatches({ limit: ORDERS_PAGE_SIZE }), fetchStats()])
      .then(([b, s]) => {
        setBatches(b.results);
        setHasMore(b.has_more);
        setStats(s);
        // Open the newest checkout by default — it's the one being followed.
        if (b.results.length > 0) setExpanded(b.results[0].id);
      })
      .catch(() => setError("שגיאה בטעינת הנתונים"));
  }, []);

  async function loadMore() {
    if (!batches) return;
    setLoadingMore(true);
    try {
      const res = await fetchOrderBatches({ limit: ORDERS_PAGE_SIZE, offset: batches.length });
      setBatches((prev) => [...(prev ?? []), ...res.results]);
      setHasMore(res.has_more);
    } catch {
      setError("שגיאה בטעינת הזמנות נוספות");
    } finally {
      setLoadingMore(false);
    }
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!batches || !stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  const maxSpend =
    stats.by_supplier.length > 0 ? Number(stats.by_supplier[0].total_spent) : 1;

  return (
    <div className="px-6 py-6 space-y-8">
      <h1 className="text-2xl font-bold text-green-900">לוח בקרה</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div className="bg-green-700 rounded-xl shadow-md p-4">
          <p className="text-xs text-green-200 mb-1">סה&quot;כ הוצאות</p>
          <p className="text-2xl font-bold text-white">
            {formatCurrency(stats.total_spent)}
          </p>
        </div>
        <div className="bg-blue-600 rounded-xl shadow-md p-4">
          <p className="text-xs text-blue-100 mb-1">מספר הזמנות</p>
          <p className="text-2xl font-bold text-white">{stats.order_count}</p>
        </div>
        <div className="bg-orange-500 rounded-xl shadow-md p-4">
          <p className="text-xs text-orange-100 mb-1">מספר ספקים</p>
          <p className="text-2xl font-bold text-white">{stats.by_supplier.length}</p>
        </div>
      </div>

      {/* Spending by supplier */}
      {stats.by_supplier.length > 0 && (
        <section>
          <h2 className="text-base font-semibold text-green-900 mb-3">הוצאות לפי ספק</h2>
          <div className="bg-white rounded-xl shadow-md divide-y divide-gray-100">
            {stats.by_supplier.map((s) => (
              <div key={s.supplier_id} className="px-4 py-3">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-medium text-gray-800">{s.supplier_name}</span>
                  <span className="text-sm font-semibold text-green-700">
                    {formatCurrency(s.total_spent)}
                  </span>
                </div>
                <div className="w-full bg-gray-100 rounded-full h-2">
                  <div
                    className="bg-green-500 h-2 rounded-full"
                    style={{ width: `${(Number(s.total_spent) / maxSpend) * 100}%` }}
                  />
                </div>
                <p className="text-xs text-gray-400 mt-1">{s.order_count} פריטים</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent orders — one row per checkout, expanding into its per-supplier orders */}
      <section>
        <h2 className="text-base font-semibold text-green-900 mb-3">הזמנות אחרונות</h2>
        {batches.length === 0 ? (
          <p className="text-sm text-gray-600">אין הזמנות עדיין.</p>
        ) : (
          <div className="space-y-2">
            {batches.map((b) => {
              const isOpen = expanded === b.id;
              const liveOrders = b.orders.filter((o) => o.status !== "cancelled");
              const progress = deliveredSummary(b.orders);
              return (
                <div key={b.id} className="bg-white rounded-xl shadow-md overflow-hidden">
                  <button
                    onClick={() => setExpanded(isOpen ? null : b.id)}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-green-50 transition text-right"
                  >
                    <div className="flex-1 grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-sm items-center min-w-0">
                      <span className="font-medium text-gray-800">{formatDateTime(b.created_at)}</span>
                      <span className="text-gray-500">
                        {liveOrders.length === 1 ? liveOrders[0].supplier_name : `${liveOrders.length} ספקים`}
                      </span>
                      <span className="font-semibold text-green-700">{formatCurrency(b.total_price)}</span>
                      <span className="flex items-center gap-2">
                        <StatusBadge status={b.status} />
                        {progress && <span className="text-xs text-gray-400">{progress}</span>}
                      </span>
                    </div>
                    <span className={`shrink-0 text-gray-400 transition-transform ${isOpen ? "rotate-180" : ""}`}>
                      ▼
                    </span>
                  </button>

                  {isOpen && (
                    <div className="border-t border-gray-100 overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-gray-50 text-gray-500 text-xs">
                            <th className="text-right px-4 py-2 font-medium">הזמנה</th>
                            <th className="text-right px-4 py-2 font-medium">ספק</th>
                            <th className="text-right px-4 py-2 font-medium">פריטים</th>
                            <th className="text-right px-4 py-2 font-medium">סה&quot;כ</th>
                            <th className="text-right px-4 py-2 font-medium">סטטוס</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50">
                          {b.orders.map((o) => (
                            <tr
                              key={o.id}
                              onClick={() => router.push(`/dashboard/orders/${o.id}`)}
                              className="hover:bg-green-50 cursor-pointer transition"
                            >
                              <td className="px-4 py-2.5 text-gray-500">#{o.id}</td>
                              <td className="px-4 py-2.5 text-gray-800">{o.supplier_name}</td>
                              <td className="px-4 py-2.5 text-gray-700">{o.product_count}</td>
                              <td className="px-4 py-2.5 font-medium text-gray-800">{formatCurrency(o.total_price)}</td>
                              <td className="px-4 py-2.5"><StatusBadge status={o.status} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {hasMore && (
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="mt-3 w-full border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition"
          >
            {loadingMore ? "טוען..." : "טען עוד"}
          </button>
        )}
      </section>
    </div>
  );
}
