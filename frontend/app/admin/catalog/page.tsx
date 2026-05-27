"use client";

import { useEffect, useState } from "react";
import { fetchProducts, deleteProduct, Product } from "../../lib/api";

export default function AdminCatalogPage() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const data = await fetchProducts();
      setProducts(data);
    } catch {
      setError("שגיאה בטעינת המוצרים.");
    }
  }

  async function handleDelete(id: number, name: string) {
    if (!confirm(`למחוק את "${name}"? הפעולה תמחק גם את מחירי השוק ומחירי הספקים שלו.`)) return;
    setDeletingId(id);
    try {
      await deleteProduct(id);
      setProducts((prev) => prev?.filter((p) => p.id !== id) ?? null);
    } catch {
      alert("שגיאה במחיקת המוצר");
    } finally {
      setDeletingId(null);
    }
  }

  const filtered = products?.filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase())
  );

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!products) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-800">ניהול קטלוג מוצרים</h1>
        <span className="text-sm text-gray-500">{products.length} מוצרים</span>
      </div>

      <input
        type="text"
        placeholder="חיפוש מוצר..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
        dir="rtl"
      />

      {filtered?.length === 0 ? (
        <p className="text-gray-500 text-sm">לא נמצאו מוצרים.</p>
      ) : (
        <div className="bg-white rounded-xl shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs">
                <th className="text-right px-4 py-2 font-medium">שם מוצר</th>
                <th className="text-right px-4 py-2 font-medium">יחידה</th>
                <th className="px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered?.map((p) => (
                <tr key={p.id} className="hover:bg-gray-50 transition">
                  <td className="px-4 py-3 font-medium text-gray-800">{p.name}</td>
                  <td className="px-4 py-3 text-gray-500">{p.unit_display}</td>
                  <td className="px-4 py-3 text-left">
                    <button
                      onClick={() => handleDelete(p.id, p.name)}
                      disabled={deletingId === p.id}
                      className="text-xs text-red-500 hover:text-red-700 disabled:opacity-40 transition"
                    >
                      {deletingId === p.id ? "מוחק..." : "מחק"}
                    </button>
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
