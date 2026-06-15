"use client";

import { useEffect, useState } from "react";
import { useAutoError } from "../../lib/useAutoError";
import {
  fetchSuppliersAll,
  createSupplier,
  updateSupplier,
  deleteSupplier,
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
  const [formError, setFormError] = useAutoError(5000);
  const [saving, setSaving] = useState(false);

  const [confirmDelete, setConfirmDelete] = useState<SupplierWithProducts | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [editSupplier, setEditSupplier] = useState<SupplierWithProducts | null>(null);
  const [editForm, setEditForm] = useState<CreateSupplierPayload>(EMPTY_FORM);
  const [editError, setEditError] = useAutoError(5000);
  const [editSaving, setEditSaving] = useState(false);

  const [whatsappLink, setWhatsappLink] = useState<{ name: string } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const WA_LINK = "https://wa.me/14155238886?text=join%20special-orbit";

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

  function openEdit(s: SupplierWithProducts) {
    setEditSupplier(s);
    setEditForm({
      name: s.name,
      phone: s.phone,
      whatsapp_number: s.whatsapp_number,
      region: s.region,
      minimum_order: String(s.minimum_order),
    });
    setEditError("");
  }

  async function handleEditSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editSupplier) return;
    setEditSaving(true);
    setEditError("");
    try {
      await updateSupplier(editSupplier.id, editForm);
      setEditSupplier(null);
      await load();
    } catch (err: unknown) {
      setEditError(err instanceof Error ? err.message : "שגיאה בעדכון הספק");
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await deleteSupplier(confirmDelete.id);
      setConfirmDelete(null);
      await load();
    } catch {
      alert("שגיאה במחיקת הספק");
    } finally {
      setDeleting(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    setSaving(true);
    try {
      await createSupplier(form);
      const createdName = form.name;
      setShowModal(false);
      setForm(EMPTY_FORM);
      await load();
      setWhatsappLink({ name: createdName });
      setLinkCopied(false);
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
              <div className="flex items-center gap-2 px-4 py-3">
                <button
                  onClick={() => toggleExpand(s.id)}
                  className="flex-1 flex items-center gap-4 hover:bg-gray-50 transition text-right"
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
                <button
                  onClick={() => openEdit(s)}
                  className="shrink-0 text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded-lg px-2 py-1 text-sm transition"
                  title="ערוך ספק"
                >
                  ✏️
                </button>
                <button
                  onClick={() => setConfirmDelete(s)}
                  className="shrink-0 text-red-500 hover:text-red-700 hover:bg-red-50 rounded-lg px-2 py-1 text-sm transition"
                  title="מחק ספק"
                >
                  🗑
                </button>
              </div>

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

      {/* Edit supplier modal */}
      {editSupplier && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">עריכת ספק</h2>
              <button onClick={() => setEditSupplier(null)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>
            <form onSubmit={handleEditSave} className="space-y-3">
              <SupField label="שם ספק" name="name" value={editForm.name} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} required />
              <div className="grid grid-cols-2 gap-3">
                <SupField label="טלפון" name="phone" value={editForm.phone} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} placeholder="+972XXXXXXXXX" />
                <SupField label="WhatsApp" name="whatsapp_number" value={editForm.whatsapp_number} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} placeholder="+972XXXXXXXXX" />
              </div>
              <SupField label="הזמנה מינימלית (₪)" name="minimum_order" type="number" value={editForm.minimum_order} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} />
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">אזור</label>
                <select
                  name="region"
                  value={editForm.region}
                  onChange={(e) => setEditForm(p => ({ ...p, region: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {REGIONS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </div>
              {editError && <p className="text-red-600 text-sm">{editError}</p>}
              <div className="flex gap-3 pt-2">
                <button type="submit" disabled={editSaving} className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                  {editSaving ? "שומר..." : "שמור שינויים"}
                </button>
                <button type="button" onClick={() => setEditSupplier(null)} className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50">
                  ביטול
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      {confirmDelete && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6" dir="rtl">
            <h2 className="text-lg font-bold text-gray-800 mb-2">מחיקת ספק</h2>
            <p className="text-sm text-gray-600 mb-1">
              האם למחוק את <span className="font-medium">{confirmDelete.name}</span>?
            </p>
            <p className="text-xs text-red-500 mb-5">
              פעולה זו תמחק גם את כל {confirmDelete.products.length} המוצרים והמחירים שלו.
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="flex-1 bg-red-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? "מוחק..." : "מחק"}
              </button>
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50"
              >
                ביטול
              </button>
            </div>
          </div>
        </div>
      )}

      {/* WhatsApp onboarding link popup */}
      {whatsappLink && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6 text-center" dir="rtl">
            <div className="text-4xl mb-3">✅</div>
            <h2 className="text-lg font-bold text-gray-800 mb-1">
              {whatsappLink.name} נוסף בהצלחה!
            </h2>
            <p className="text-sm text-gray-500 mb-4">
              שלח לספק את הקישור הבא כדי שיוכל להתחבר לבוט:
            </p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm text-blue-600 break-all mb-3 font-mono select-all">
              {WA_LINK}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => {
                  navigator.clipboard.writeText(WA_LINK);
                  setLinkCopied(true);
                }}
                className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-700 hover:bg-gray-50 transition"
              >
                {linkCopied ? "✓ הועתק!" : "העתק קישור"}
              </button>
              <a
                href={WA_LINK}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 bg-green-500 text-white rounded-lg py-2 text-sm font-medium hover:bg-green-600 transition flex items-center justify-center gap-1"
              >
                פתח וואטסאפ
              </a>
            </div>
            <button
              onClick={() => setWhatsappLink(null)}
              className="mt-3 text-sm text-gray-400 hover:text-gray-600"
            >
              סגור
            </button>
          </div>
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
                <SupField label="טלפון" name="phone" value={form.phone} onChange={handleChange} placeholder="+972XXXXXXXXX" />
                <SupField label="WhatsApp" name="whatsapp_number" value={form.whatsapp_number} onChange={handleChange} placeholder="+972XXXXXXXXX" />
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
  label, name, value, onChange, type = "text", required = false, placeholder,
}: {
  label: string; name: string; value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  type?: string; required?: boolean; placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type} name={name} value={value} onChange={onChange}
        required={required} placeholder={placeholder}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}
