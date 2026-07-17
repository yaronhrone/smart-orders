"use client";

import { useEffect, useState } from "react";
import { fetchProducts, deleteProduct, createProduct, Product } from "../../lib/api";
import { useAutoError } from "../../lib/useAutoError";

const UNITS = [
  { value: "kg",    label: 'ק"ג' },
  { value: "gram",  label: "גרם" },
  { value: "unit",  label: "יחידה" },
  { value: "box",   label: "ארגז" },
  { value: "liter", label: "ליטר" },
  { value: "meter", label: "מטר" },
];

const EMPTY_FORM = { name: "", unit: "kg" };

export default function AdminCatalogPage() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useAutoError(6000);
  const [saving, setSaving] = useState(false);

  useEffect(() => { load(); }, []);

  async function load() {
    try {
      const data = await fetchProducts();
      setProducts(data);
    } catch {
      setError("שגיאה בטעינת המוצרים.");
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    setSaving(true);
    try {
      await createProduct(form);
      setShowModal(false);
      setForm(EMPTY_FORM);
      await load();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "שגיאה ביצירת המוצר");
    } finally {
      setSaving(false);
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
        <button
          onClick={() => { setForm(EMPTY_FORM); setFormError(""); setShowModal(true); }}
          className="bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700 transition"
        >
          + הוסף מוצר
        </button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="חיפוש מוצר..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
          dir="rtl"
        />
        <span className="text-sm text-gray-400 whitespace-nowrap">{products.length} מוצרים</span>
      </div>

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

      {showModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">הוספת מוצר לקטלוג</h2>
              <button
                onClick={() => setShowModal(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">שם מוצר</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  required
                  placeholder="לדוגמה: עגבנייה"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="text-xs text-gray-400 mt-1">
                  השם יהיה השם הקנוני — הAI יתאים הודעות ספקים לשם זה
                </p>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">יחידת מידה</label>
                <select
                  value={form.unit}
                  onChange={(e) => setForm((p) => ({ ...p, unit: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {UNITS.map((u) => (
                    <option key={u.value} value={u.value}>{u.label}</option>
                  ))}
                </select>
              </div>

              {formError && <p className="text-red-600 text-sm">{formError}</p>}

              <div className="flex gap-3 pt-1">
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
                >
                  {saving ? "שומר..." : "הוסף מוצר"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50 transition"
                >
                  ביטול
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
