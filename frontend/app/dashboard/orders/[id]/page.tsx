"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { fetchOrderDetail, OrderDetail } from "../../../lib/api";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending:   { label: "ממתין",  color: "bg-yellow-100 text-yellow-800" },
  confirmed: { label: "אושר",   color: "bg-blue-100 text-blue-800" },
  delivered: { label: "נמסר",   color: "bg-green-100 text-green-800" },
  cancelled: { label: "בוטל",   color: "bg-red-100 text-red-800" },
  PENDING:   { label: "ממתין",  color: "bg-yellow-100 text-yellow-800" },
  CONFIRMED: { label: "אושר",   color: "bg-blue-100 text-blue-800" },
  DELIVERED: { label: "נמסר",   color: "bg-green-100 text-green-800" },
  CANCELLED: { label: "בוטל",   color: "bg-red-100 text-red-800" },
};

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("he-IL", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function groupBySupplier(order: OrderDetail) {
  const groups: Record<string, { name: string; items: OrderDetail["products"] }> = {};
  for (const item of order.products) {
    const key = String(item.supplier_id);
    if (!groups[key]) groups[key] = { name: item.supplier_name, items: [] };
    groups[key].items.push(item);
  }
  return Object.values(groups);
}

export default function OrderDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = Number(params.id);

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { router.push("/login"); return; }
    fetchOrderDetail(id)
      .then(setOrder)
      .catch(() => setError("שגיאה בטעינת ההזמנה"));
  }, [id, router]);

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center" dir="rtl">
        <p className="text-red-600">{error}</p>
      </main>
    );
  }

  if (!order) {
    return (
      <main className="min-h-screen flex items-center justify-center" dir="rtl">
        <p className="text-gray-500">טוען...</p>
      </main>
    );
  }

  const s = STATUS_LABELS[order.status] ?? { label: order.status, color: "bg-gray-100 text-gray-700" };
  const groups = groupBySupplier(order);

  return (
    <main className="min-h-screen bg-gray-50" dir="rtl">
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
          <button
            onClick={() => router.push("/dashboard")}
            className="text-sm text-gray-500 hover:text-gray-800 transition"
          >
            ← חזרה
          </button>
          <h1 className="text-lg font-bold text-gray-800">הזמנה #{order.id}</h1>
        </div>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">

        {/* Summary card */}
        <div className="bg-white rounded-xl shadow-sm p-5 flex flex-wrap gap-6">
          <div>
            <p className="text-xs text-gray-500 mb-1">תאריך</p>
            <p className="text-sm font-medium text-gray-800">{formatDate(order.created_at)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">סטטוס</p>
            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${s.color}`}>
              {s.label}
            </span>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">ספקים</p>
            <p className="text-sm font-medium text-gray-800">{groups.length}</p>
          </div>
          <div className="mr-auto">
            <p className="text-xs text-gray-500 mb-1">סה"כ להזמנה</p>
            <p className="text-2xl font-bold text-gray-900">{formatCurrency(order.total_price)}</p>
          </div>
        </div>

        {/* Per-supplier breakdown */}
        {groups.map((group) => {
          const groupTotal = group.items.reduce(
            (sum, item) => sum + Number(item.subtotal),
            0
          );
          return (
            <section key={group.name}>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-base font-semibold text-gray-700">{group.name}</h2>
                <span className="text-sm font-semibold text-gray-900">{formatCurrency(groupTotal)}</span>
              </div>
              <div className="bg-white rounded-xl shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-gray-500 text-xs">
                      <th className="text-right px-4 py-2 font-medium">מוצר</th>
                      <th className="text-right px-4 py-2 font-medium">כמות</th>
                      <th className="text-right px-4 py-2 font-medium">מחיר יחידה</th>
                      <th className="text-right px-4 py-2 font-medium">סה"כ</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {group.items.map((item) => (
                      <tr key={item.product_id}>
                        <td className="px-4 py-3 font-medium text-gray-800">{item.product_name}</td>
                        <td className="px-4 py-3 text-gray-600">{Number(item.quantity)}</td>
                        <td className="px-4 py-3 text-gray-600">{formatCurrency(item.unit_price)}</td>
                        <td className="px-4 py-3 font-semibold text-gray-800">{formatCurrency(item.subtotal)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })}
      </div>
    </main>
  );
}
