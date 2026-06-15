"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchOrders, fetchStats, OrderSummary, OrderStats } from "../lib/api";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending:   { label: "ממתין",  color: "bg-yellow-100 text-yellow-800" },
  approved:  { label: "אושר",   color: "bg-blue-100 text-blue-800" },
  sent:      { label: "נשלח",   color: "bg-purple-100 text-purple-800" },
  delivered: { label: "נמסר",   color: "bg-green-100 text-green-800" },
  cancelled: { label: "בוטל",   color: "bg-red-100 text-red-800" },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_LABELS[status] ?? { label: status, color: "bg-gray-100 text-gray-700" };
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${s.color}`}>
      {s.label}
    </span>
  );
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("he-IL", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function DashboardPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [stats, setStats] = useState<OrderStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([fetchOrders(), fetchStats()])
      .then(([o, s]) => {
        setOrders(o);
        setStats(s);
      })
      .catch(() => setError("שגיאה בטעינת הנתונים"));
  }, []);

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!orders || !stats) {
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
      <h1 className="text-xl font-bold text-gray-800">לוח בקרה</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div className="bg-white rounded-xl shadow-sm p-4">
          <p className="text-xs text-gray-500 mb-1">סה&quot;כ הוצאות</p>
          <p className="text-2xl font-bold text-gray-800">
            {formatCurrency(stats.total_spent)}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-4">
          <p className="text-xs text-gray-500 mb-1">מספר הזמנות</p>
          <p className="text-2xl font-bold text-gray-800">{stats.order_count}</p>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-4">
          <p className="text-xs text-gray-500 mb-1">מספר ספקים</p>
          <p className="text-2xl font-bold text-gray-800">{stats.by_supplier.length}</p>
        </div>
      </div>

      {/* Spending by supplier */}
      {stats.by_supplier.length > 0 && (
        <section>
          <h2 className="text-base font-semibold text-gray-700 mb-3">הוצאות לפי ספק</h2>
          <div className="bg-white rounded-xl shadow-sm divide-y divide-gray-100">
            {stats.by_supplier.map((s) => (
              <div key={s.supplier_id} className="px-4 py-3">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-medium text-gray-800">{s.supplier_name}</span>
                  <span className="text-sm font-semibold text-gray-900">
                    {formatCurrency(s.total_spent)}
                  </span>
                </div>
                <div className="w-full bg-gray-100 rounded-full h-1.5">
                  <div
                    className="bg-blue-500 h-1.5 rounded-full"
                    style={{ width: `${(Number(s.total_spent) / maxSpend) * 100}%` }}
                  />
                </div>
                <p className="text-xs text-gray-400 mt-1">{s.order_count} פריטים</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent orders */}
      <section>
        <h2 className="text-base font-semibold text-gray-700 mb-3">הזמנות אחרונות</h2>
        {orders.length === 0 ? (
          <p className="text-sm text-gray-500">אין הזמנות עדיין.</p>
        ) : (
          <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-xs">
                  <th className="text-right px-4 py-2 font-medium">#</th>
                  <th className="text-right px-4 py-2 font-medium">תאריך</th>
                  <th className="text-right px-4 py-2 font-medium">פריטים</th>
                  <th className="text-right px-4 py-2 font-medium">סה&quot;כ</th>
                  <th className="text-right px-4 py-2 font-medium">סטטוס</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {orders.map((o) => (
                  <tr
                    key={o.id}
                    onClick={() => router.push(`/dashboard/orders/${o.id}`)}
                    className="hover:bg-blue-50 cursor-pointer transition"
                  >
                    <td className="px-4 py-3 text-gray-500">{o.id}</td>
                    <td className="px-4 py-3 text-gray-700">{formatDate(o.created_at)}</td>
                    <td className="px-4 py-3 text-gray-700">{o.product_count}</td>
                    <td className="px-4 py-3 font-medium text-gray-800">
                      {formatCurrency(o.total_price)}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={o.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
