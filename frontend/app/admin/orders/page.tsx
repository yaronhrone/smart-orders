"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchAdminOrders, updateOrderStatus, AdminOrderSummary } from "../../lib/api";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending:   { label: "ממתין",       color: "bg-yellow-100 text-yellow-800" },
  approved:  { label: "אושר",        color: "bg-blue-100 text-blue-800" },
  sent:      { label: "נשלח",        color: "bg-purple-100 text-purple-800" },
  shipped:   { label: "יצא למשלוח",  color: "bg-indigo-100 text-indigo-800" },
  delivered: { label: "נמסר",        color: "bg-green-100 text-green-800" },
  cancelled: { label: "בוטל",        color: "bg-red-100 text-red-800" },
};

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
  { value: "", label: "פתוחות (לא נמסרו/בוטלו)" },
  { value: "pending", label: "ממתין" },
  { value: "approved", label: "אושר" },
  { value: "sent", label: "נשלח" },
  { value: "shipped", label: "יצא למשלוח" },
  { value: "delivered", label: "נמסר" },
  { value: "cancelled", label: "בוטל" },
];

const ORDERS_PAGE_SIZE = 20;

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_LABELS[status] ?? { label: status, color: "bg-gray-100 text-gray-700" };
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${s.color}`}>
      {s.label}
    </span>
  );
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" });
}

export default function AdminOrdersPage() {
  const [orders, setOrders] = useState<AdminOrderSummary[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [pendingNext, setPendingNext] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    try {
      const res = await fetchAdminOrders({ limit: ORDERS_PAGE_SIZE, status: statusFilter || undefined });
      setOrders(res.results);
      setHasMore(res.has_more);
    } catch {
      setError("שגיאה בטעינת ההזמנות.");
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  async function loadMore() {
    if (!orders) return;
    setLoadingMore(true);
    try {
      const res = await fetchAdminOrders({
        limit: ORDERS_PAGE_SIZE, offset: orders.length, status: statusFilter || undefined,
      });
      setOrders((prev) => [...(prev ?? []), ...res.results]);
      setHasMore(res.has_more);
    } catch {
      alert("שגיאה בטעינת הזמנות נוספות");
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleUpdate(order: AdminOrderSummary) {
    const next = pendingNext[order.id];
    if (!next) return;
    const label = STATUS_LABELS[next]?.label ?? next;
    if (!confirm(`לעדכן הזמנה #${order.id} ל"${label}"? זו פעולת בדיקה — היא לא שולחת שום הודעה לספק או ללקוח.`)) return;

    setUpdatingId(order.id);
    try {
      await updateOrderStatus(order.id, next);
      // Refetch rather than patch in place — a status change likely moves
      // the row out of the current filter (e.g. "פתוחות" once delivered).
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

  if (!orders) {
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
        לעדכון סטטוס ידני של הזמנה — בעיקר כדי לשחרר הזמנת בדיקה שאין לה ספק
        אמיתי שיכול לאשר אותה בוואטסאפ. עדכון כאן לא שולח שום הודעה, רק משנה
        את הסטטוס במערכת.
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
        <span className="text-sm text-gray-400 whitespace-nowrap">מוצגות {orders.length} הזמנות</span>
      </div>

      {orders.length === 0 ? (
        <p className="text-gray-500 text-sm">לא נמצאו הזמנות.</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm overflow-hidden overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs">
                <th className="text-right px-4 py-2 font-medium">#</th>
                <th className="text-right px-4 py-2 font-medium">לקוח</th>
                <th className="text-right px-4 py-2 font-medium">סטטוס</th>
                <th className="text-right px-4 py-2 font-medium">סה&quot;כ</th>
                <th className="text-right px-4 py-2 font-medium">נוצרה</th>
                <th className="px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {orders.map((o) => {
                const options = ALLOWED_NEXT[o.status] ?? [];
                return (
                  <tr key={o.id} className="hover:bg-gray-50 transition">
                    <td className="px-4 py-3 font-medium text-gray-800">#{o.id}</td>
                    <td className="px-4 py-3 text-gray-700">
                      <div>{o.company_name || "—"}</div>
                      <div className="text-xs text-gray-400">{o.customer_email}</div>
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                    <td className="px-4 py-3 text-gray-700">₪{o.total_price}</td>
                    <td className="px-4 py-3 text-gray-500 whitespace-nowrap">{formatDate(o.created_at)}</td>
                    <td className="px-4 py-3">
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
