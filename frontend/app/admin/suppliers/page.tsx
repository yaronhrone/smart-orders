"use client";

import { useEffect, useState } from "react";
import {
  fetchSuppliersAll,
  createSupplier,
  SupplierWithProducts,
  CreateSupplierPayload,
} from "../../lib/api";

const REGIONS = [
  { value: "tel_aviv", label: "תל אביב" },
  { value: "jerusalem", label: "ירושלים" },
  { value: "haifa", label: "חיפה" },
  { value: "south", label: "דרום" },
  { value: "north", label: "צפון" },
  { value: "center", label: "מרכז" },
];

const REGION_LABEL: Record<string, string> = Object.fromEntries(
  REGIONS.map((r) => [r.value, r.label])
);

const EMPTY_FORM: CreateSupplierPayload = {
  name: "",
  phone: "",
  whatsapp_number: "",
  region: "center",
  minimum_order: "0",
};

function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<SupplierWithProducts[] | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<CreateSupplierPayload>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const data = await fetchSuppliersAll();
      setSuppliers(data);
    } catch {
      setError("שגיאה בטעינת הספקים");
    }
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => (prev === id ? null : id));
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    setSaving(true);
    try {
      await createSupplier(form);
      setShowModal(false);
      setForm(EMPTY_FORM);
      await load();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "שגיאה ביצירת הספק");
    } finally {
      setSaving(false);
    }
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!suppliers) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-800">ניהול ספקים</h1>
        <button
          onClick={() => { setForm(EMPTY_FORM); setFormError(""); setShowModal(true); }}
          className="bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700 transition"
        >
          + הוסף ספק
        </button>
      </div>

      <p className="text-sm text-gray-500 mb-4">{suppliers.length} ספקים רשומים</p>

      {suppliers.length === 0 ? (
        <p className="text-gray-500 text-sm">אין ספקים עדיין.</p>
      ) : (
        <div className="space-y-2">
          {suppliers.map((s) => (
            <div key={s.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
              {/* Supplier row */}
              <button
                onClick={() => toggleExpand(s.id)}
                className="w-full flex items-center gap-4 px-4 py-3 hover:bg-gray-50 transition text-right"
              >
                <div className="flex-1 grid grid-cols-4 gap-4 text-sm">
                  <span className="font-medium text-gray-800">{s.name}</span>
                  <span className="text-gray-500">{REGION_LABEL[s.region] ?? s.region}</span>
                  <span className="text-gray-500">{s.phone}</span>
                  <span className="text-gray-500">
                    מינימום: {formatCurrency(s.minimum_order)}
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                    {s.products.length} מוצרים
                  </span>
                  <span className={`text-gray-400 transition-transform ${expanded === s.id ? "rotate-180" : ""}`}>
                    ▼
                  </span>
                </div>
              </button>

              {/* Expanded products */}
              {expanded === s.id && s.products.length > 0 && (
                <div className="border-t border-gray-100">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-gray-50 text-gray-500 text-xs">
                        <th className="text-right px-4 py-2 font-medium">מוצר</th>
                        <th className="text-right px-4 py-2 font-medium">יחידה</th>
                        <th className="text-right px-4 py-2 font-medium">מחיר ליחידה</th>
                        <th className="text-right px-4 py-2 font-medium">עדכון אחרון</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {s.products.map((p) => (
                        <tr key={p.product_id} className="hover:bg-gray-50 transition">
                          <td className="px-4 py-2.5 text-gray-800">{p.product_name}</td>
                          <td className="px-4 py-2.5 text-gray-500">{p.unit}</td>
                          <td className="px-4 py-2.5 font-medium text-gray-800">
                            {formatCurrency(p.price_per_unit)}
                          </td>
                          <td className="px-4 py-2.5 text-gray-400 text-xs">
                            {new Date(p.updated_at).toLocaleDateString("he-IL")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {expanded === s.id && s.products.length === 0 && (
                <div className="border-t border-gray-100 px-4 py-3 text-sm text-gray-400">
                  אין מוצרים רשומים לספק זה
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">הוספת ספק חדש</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>

            <form onSubmit={handleCreate} className="space-y-3">
              <SupField label="שם ספק" name="name" value={form.name} onChange={handleChange} required />
              <div className="grid grid-cols-2 gap-3">
                <SupField label="טלפון" name="phone" value={form.phone} onChange={handleChange} />
                <SupField label="WhatsApp" name="whatsapp_number" value={form.whatsapp_number} onChange={handleChange} />
              </div>
              <SupField
                label="הזמנה מינימלית (₪)"
                name="minimum_order"
                type="number"
                value={form.minimum_order}
                onChange={handleChange}
              />
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">אזור</label>
                <select
                  name="region"
                  value={form.region}
                  onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {REGIONS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </div>

              {formError && <p className="text-red-600 text-sm">{formError}</p>}

              <div className="flex gap-3 pt-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? "שומר..." : "צור ספק"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50"
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

function SupField({
  label,
  name,
  value,
  onChange,
  type = "text",
  required = false,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        name={name}
        value={value}
        onChange={onChange}
        required={required}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}
