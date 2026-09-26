"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { fetchOrderDetail, updateOrderStatus, OrderDetail } from "../../../lib/api";
import { StatusBadge, formatCurrency, formatDateTime } from "../../../lib/orderStatus";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("he-IL", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Statuses a customer can still confirm as received from — mirrors the
// backend's ALLOWED_TRANSITIONS into DELIVERED.
const RECEIVABLE = ["sent", "approved", "shipped"];

export default function OrderDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = Number(params.id);

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState("");
  const [marking, setMarking] = useState(false);

  useEffect(() => {
    fetchOrderDetail(id).then(setOrder).catch(() => setError("שגיאה בטעינת ההזמנה"));
  }, [id]);

  async function handleMarkDelivered() {
    if (!order) return;
    setMarking(true);
    try {
      await updateOrderStatus(order.id, "delivered");
      setOrder({ ...order, status: "delivered" });
    } catch {
      setError("שגיאה בעדכון הסטטוס");
    } finally {
      setMarking(false);
    }
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!order) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6 max-w-3xl space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.push("/dashboard")}
          className="text-sm text-gray-500 hover:text-gray-800 transition"
        >
          &larr; חזרה
        </button>
        <h1 className="text-xl font-bold text-gray-800">
          הזמנה #{order.id} — {order.supplier_name}
        </h1>
      </div>

      <p className="text-sm text-gray-500">
        חלק מההזמנה של {formatDateTime(order.batch_created_at)} — כל ספק מקבל הזמנה נפרדת.
      </p>

      {/* Summary card */}
      <div className="bg-white rounded-xl shadow-sm p-5 flex flex-wrap gap-6">
        <div>
          <p className="text-xs text-gray-500 mb-1">תאריך</p>
          <p className="text-sm font-medium text-gray-800">{formatDate(order.created_at)}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-1">ספק</p>
          <p className="text-sm font-medium text-gray-800">{order.supplier_name}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-1">סטטוס</p>
          <StatusBadge status={order.status} />
        </div>
        <div className="mr-auto">
          <p className="text-xs text-gray-500 mb-1">סה&quot;כ להזמנה</p>
          <p className="text-2xl font-bold text-gray-900">{formatCurrency(order.total_price)}</p>
        </div>
      </div>

      {/* קיבלתי — per order, since each supplier delivers on its own */}
      {RECEIVABLE.includes(order.status) && (
        <button
          onClick={handleMarkDelivered}
          disabled={marking}
          className="w-full bg-green-600 text-white rounded-xl py-3 text-sm font-semibold hover:bg-green-700 disabled:opacity-50 transition"
        >
          {marking ? "מעדכן..." : `✅ קיבלתי את ההזמנה מ-${order.supplier_name}`}
        </button>
      )}

      {/* Items */}
      <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-gray-500 text-xs">
              <th className="text-right px-4 py-2 font-medium">מוצר</th>
              <th className="text-right px-4 py-2 font-medium">כמות / יחידה</th>
              <th className="text-right px-4 py-2 font-medium">מחיר יחידה</th>
              <th className="text-right px-4 py-2 font-medium">סה&quot;כ</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {order.products.map((item) => (
              <tr key={item.product_id}>
                <td className="px-4 py-3 font-medium text-gray-800">{item.product_name}</td>
                <td className="px-4 py-3 text-gray-600">{Number(item.quantity)} {item.unit_display}</td>
                <td className="px-4 py-3 text-gray-600">{formatCurrency(item.unit_price)}</td>
                <td className="px-4 py-3 font-semibold text-gray-800">{formatCurrency(item.subtotal)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {order.products.length === 0 && (
          <p className="px-4 py-3 text-sm text-gray-400">אין פריטים בהזמנה זו (הועברו לספק אחר או בוטלו).</p>
        )}
      </div>
    </div>
  );
}
