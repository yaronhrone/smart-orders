"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchAdminOrderBatches,
  updateOrderStatus,
  AdminOrderBatchSummary,
  BatchOrderSummary,
} from "../../lib/api";
import {
  STATUS_LABELS,
  StatusBadge,
  deliveredSummary,
  formatCurrency,
  formatDateTime,
} from "../../lib/orderStatus";

// Mirrors OrderRequest.ALLOWED_TRANSITIONS on the backend — the server is
// still the real gate (it re-validates), this is only so the dropdown
// doesn't offer a transition that's guaranteed to be rejected.
const ALLOWED_NEXT: Record<string, string[]> = {
  pending: ["sent", "cancelled"],
  sent: ["approved", "delivered", "cancelled"],
  approved: ["shipped", "delivered", "cancelled"],
  shipped: ["delivered", "cancelled"],
  delivered: [],
  cancelled: [],
};

const STATUS_FILTERS = [
  { value: "", label: "פתוחות (יש הזמנה שלא נמסרה/בוטלה)" },
  { value: "pending", label: "ממתין" },
  { value: "sent", label: "נשלח לספק" },
  { value: "approved", label: "אושר" },
  { value: "shipped", label: "יצא למשלוח" },
  { value: "delivered", label: "נמסר" },
  { value: "cancelled", label: "בוטל" },
];

const BATCHES_PAGE_SIZE = 20;

export default function AdminOrdersPage() {
  const [batches, setBatches] = useState<AdminOrderBatchSummary[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [pendingNext, setPendingNext] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    try {
      const res = await fetchAdminOrderBatches({ limit: BATCHES_PAGE_SIZE, status: statusFilter || undefined });
      setBatches(res.results);
      setHasMore(res.has_more);
    } catch {
      setError("שגיאה בטעינת ההזמנות.");
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  async function loadMore() {
    if (!batches) return;
    setLoadingMore(true);
    try {
      const res = await fetchAdminOrderBatches({
        limit: BATCHES_PAGE_SIZE, offset: batches.length, status: statusFilter || undefined,
      });
      setBatches((prev) => [...(prev ?? []), ...res.results]);
      setHasMore(res.has_more);
    } catch {
      alert("שגיאה בטעינת הזמנות נוספות");
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleUpdate(order: BatchOrderSummary) {
    const next = pendingNext[order.id];
    if (!next) return;
    const label = STATUS_LABELS[next]?.label ?? next;
    if (!confirm(
      `לעדכן הזמנה #${order.id} (${order.supplier_name}) ל"${label}"? ` +
      "זו פעולת בדיקה — היא לא שולחת שום הודעה לספק או ללקוח, ולא נוגעת בהזמנות האחרות מאותה קבוצה."
    )) return;

    setUpdatingId(order.id);
    try {
      await updateOrderStatus(order.id, next);
      setPendingNext((prev) => ({ ...prev, [order.id]: "" }));
      // Refetch rather than patch in place — a status change can move the
      // whole checkout out of the current filter (e.g. "פתוחות" once all delivered).
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "שגיאה בעדכון הסטטוס");
    } finally {
      setUpdatingId(null);
    }
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!batches) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-800">ניהול הזמנות</h1>
      </div>

      <p className="text-xs text-gray-400 mb-4 max-w-2xl">
        כל שורה היא הזמנה אחת של לקוח; בפתיחה — הזמנה נפרדת לכל ספק, כל אחת עם
        סטטוס משלה. עדכון סטטוס כאן הוא ידני (בעיקר לשחרור הזמנת בדיקה שאין לה
        ספק אמיתי שיכול לאשר בוואטסאפ) — הוא לא שולח שום הודעה.
      </p>

      <div className="flex items-center gap-3 mb-4">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
          dir="rtl"
        >
          {STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <span className="text-sm text-gray-400 whitespace-nowrap">מוצגות {batches.length} הזמנות</span>
      </div>

      {batches.length === 0 ? (
        <p className="text-gray-500 text-sm">לא נמצאו הזמנות.</p>
      ) : (
        <div className="space-y-2">
          {batches.map((b) => {
            const isOpen = expanded === b.id;
            const progress = deliveredSummary(b.orders);
            return (
              <div key={b.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
                <button
                  onClick={() => setExpanded(isOpen ? null : b.id)}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition text-right"
                >
                  <div className="flex-1 grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-1 text-sm items-center min-w-0">
                    <span className="text-gray-500 whitespace-nowrap">{formatDateTime(b.created_at)}</span>
                    <span className="min-w-0">
                      <span className="block font-medium text-gray-800 truncate">{b.company_name || "—"}</span>
                      <span className="block text-xs text-gray-400 truncate">{b.customer_email}</span>
                    </span>
                    <span className="text-gray-500">{b.orders.length} ספקים</span>
                    <span className="text-gray-800">{formatCurrency(b.total_price)}</span>
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
                          <th className="px-4 py-2 font-medium"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {b.orders.map((o) => {
                          const options = ALLOWED_NEXT[o.status] ?? [];
                          return (
                            <tr key={o.id} className="hover:bg-gray-50 transition">
                              <td className="px-4 py-2.5 font-medium text-gray-800">#{o.id}</td>
                              <td className="px-4 py-2.5 text-gray-800">{o.supplier_name}</td>
                              <td className="px-4 py-2.5 text-gray-600">{o.product_count}</td>
                              <td className="px-4 py-2.5 text-gray-700">{formatCurrency(o.total_price)}</td>
                              <td className="px-4 py-2.5"><StatusBadge status={o.status} /></td>
                              <td className="px-4 py-2.5">
                                {options.length > 0 ? (
                                  <div className="flex items-center gap-2 justify-end">
                                    <select
                                      value={pendingNext[o.id] ?? ""}
                                      onChange={(e) =>
                                        setPendingNext((prev) => ({ ...prev, [o.id]: e.target.value }))
                                      }
                                      className="border border-gray-300 rounded-lg px-2 py-1 text-xs text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                      dir="rtl"
                                    >
                                      <option value="">שנה ל...</option>
                                      {options.map((s) => (
                                        <option key={s} value={s}>{STATUS_LABELS[s]?.label ?? s}</option>
                                      ))}
                                    </select>
                                    <button
                                      onClick={() => handleUpdate(o)}
                                      disabled={!pendingNext[o.id] || updatingId === o.id}
                                      className="text-xs bg-blue-600 text-white px-3 py-1 rounded-lg hover:bg-blue-700 disabled:opacity-40 transition"
                                    >
                                      {updatingId === o.id ? "מעדכן..." : "עדכן"}
                                    </button>
                                  </div>
                                ) : (
                                  <span className="text-xs text-gray-300">—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
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
    </div>
  );
}
