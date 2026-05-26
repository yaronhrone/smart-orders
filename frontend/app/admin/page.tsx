"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchAdminUsers,
  createUser,
  deleteUser,
  AdminUser,
  CreateUserPayload,
} from "../lib/api";

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

const EMPTY_FORM: CreateUserPayload = {
  email: "",
  password: "",
  password2: "",
  first_name: "",
  last_name: "",
  company_name: "",
  company_phone: "",
  company_address: "",
  phone: "",
  position: "",
  region: "center",
};

export default function AdminPage() {
  const router = useRouter();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<CreateUserPayload>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    load();
  }, [router]);

  async function load() {
    try {
      const data = await fetchAdminUsers();
      setUsers(data);
    } catch {
      setError("שגיאה בטעינת הלקוחות. ודא שהמשתמש שלך הוא admin.");
    }
  }

  function logout() {
    localStorage.removeItem("token");
    router.push("/login");
  }

  function openModal() {
    setForm(EMPTY_FORM);
    setFormError("");
    setShowModal(true);
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    if (form.password !== form.password2) {
      setFormError("הסיסמאות אינן תואמות");
      return;
    }
    setSaving(true);
    try {
      await createUser(form);
      setShowModal(false);
      await load();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "שגיאה ביצירת הלקוח";
      setFormError(msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number, name: string) {
    if (!confirm(`למחוק את ${name}?`)) return;
    setDeletingId(id);
    try {
      await deleteUser(id);
      setUsers((prev) => prev?.filter((u) => u.id !== id) ?? null);
    } catch {
      alert("שגיאה במחיקת הלקוח");
    } finally {
      setDeletingId(null);
    }
  }

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center" dir="rtl">
        <p className="text-red-600">{error}</p>
      </main>
    );
  }

  if (!users) {
    return (
      <main className="min-h-screen flex items-center justify-center" dir="rtl">
        <p className="text-gray-500">טוען...</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50" dir="rtl">
      {/* Header */}
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex justify-between items-center">
          <h1 className="text-lg font-bold text-gray-800">Smart Orders — ניהול לקוחות</h1>
          <div className="flex gap-4 items-center">
            <button
              onClick={openModal}
              className="bg-blue-600 text-white text-sm font-medium px-4 py-1.5 rounded-lg hover:bg-blue-700 transition"
            >
              + הוסף לקוח
            </button>
            <button onClick={logout} className="text-sm text-gray-500 hover:text-gray-800 transition">
              יציאה
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-4 py-6">
        <p className="text-sm text-gray-500 mb-4">{users.length} לקוחות רשומים</p>

        {users.length === 0 ? (
          <p className="text-gray-500 text-sm">אין לקוחות עדיין. לחץ על &quot;הוסף לקוח&quot;.</p>
        ) : (
          <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-xs">
                  <th className="text-right px-4 py-2 font-medium">שם</th>
                  <th className="text-right px-4 py-2 font-medium">אימייל</th>
                  <th className="text-right px-4 py-2 font-medium">חברה</th>
                  <th className="text-right px-4 py-2 font-medium">אזור</th>
                  <th className="text-right px-4 py-2 font-medium">טלפון</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50 transition">
                    <td className="px-4 py-3 font-medium text-gray-800">
                      {u.first_name} {u.last_name}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{u.email}</td>
                    <td className="px-4 py-3 text-gray-600">{u.profile?.company_name ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">
                      {u.profile?.region ? (REGION_LABEL[u.profile.region] ?? u.profile.region) : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{u.profile?.phone ?? "—"}</td>
                    <td className="px-4 py-3 text-left">
                      <button
                        onClick={() => handleDelete(u.id, `${u.first_name} ${u.last_name}`)}
                        disabled={deletingId === u.id}
                        className="text-xs text-red-500 hover:text-red-700 disabled:opacity-40 transition"
                      >
                        מחק
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">הוספת לקוח חדש</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>

            <form onSubmit={handleCreate} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="שם פרטי" name="first_name" value={form.first_name} onChange={handleChange} required />
                <Field label="שם משפחה" name="last_name" value={form.last_name} onChange={handleChange} required />
              </div>
              <Field label="אימייל" name="email" type="email" value={form.email} onChange={handleChange} required />
              <div className="grid grid-cols-2 gap-3">
                <Field label="סיסמה" name="password" type="password" value={form.password} onChange={handleChange} required />
                <Field label="אימות סיסמה" name="password2" type="password" value={form.password2} onChange={handleChange} required />
              </div>

              <hr className="my-1" />

              <Field label="שם חברה" name="company_name" value={form.company_name} onChange={handleChange} required />
              <div className="grid grid-cols-2 gap-3">
                <Field label="טלפון חברה" name="company_phone" value={form.company_phone} onChange={handleChange} />
                <Field label="טלפון אישי" name="phone" value={form.phone} onChange={handleChange} />
              </div>
              <Field label="כתובת" name="company_address" value={form.company_address} onChange={handleChange} />
              <Field label="תפקיד" name="position" value={form.position} onChange={handleChange} />

              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">אזור</label>
                <select
                  name="region"
                  value={form.region}
                  onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
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
                  {saving ? "שומר..." : "צור לקוח"}
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
    </main>
  );
}

function Field({
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
        autoComplete={type === "password" ? "new-password" : type === "email" ? "email" : "off"}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}
