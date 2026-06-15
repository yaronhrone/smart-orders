"use client";

import { useEffect, useState } from "react";
import { useAutoError } from "../lib/useAutoError";
import {
  fetchAdminUsers,
  createUser,
  deleteUser,
  updateAdminUserProfile,
  AdminUser,
  CreateUserPayload,
  UserProfile,
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

const EMPTY_PROFILE: UserProfile = {
  company_name: "",
  company_phone: "",
  company_address: "",
  phone: "",
  position: "",
  region: "center",
};

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  // Create modal
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<CreateUserPayload>(EMPTY_FORM);
  const [formError, setFormError] = useAutoError(5000);
  const [saving, setSaving] = useState(false);

  // Delete
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // Edit profile modal
  const [editUser, setEditUser] = useState<AdminUser | null>(null);
  const [editForm, setEditForm] = useState<UserProfile>(EMPTY_PROFILE);
  const [editError, setEditError] = useAutoError(5000);
  const [editSaving, setEditSaving] = useState(false);

  const [whatsappLink, setWhatsappLink] = useState<{ name: string } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const WA_LINK = "https://wa.me/14155238886?text=join%20special-orbit";

  useEffect(() => { load(); }, []);

  async function load() {
    try {
      setUsers(await fetchAdminUsers());
    } catch {
      setError("שגיאה בטעינת הלקוחות. ודא שהמשתמש שלך הוא admin.");
    }
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => (prev === id ? null : id));
  }

  function openEdit(u: AdminUser) {
    setEditUser(u);
    setEditForm({
      company_name: u.profile?.company_name ?? "",
      company_phone: u.profile?.company_phone ?? "",
      company_address: u.profile?.company_address ?? "",
      phone: u.profile?.phone ?? "",
      position: u.profile?.position ?? "",
      region: u.profile?.region ?? "center",
    });
    setEditError("");
  }

  async function handleEditSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editUser) return;
    setEditSaving(true);
    setEditError("");
    try {
      await updateAdminUserProfile(editUser.id, editForm);
      setEditUser(null);
      await load();
    } catch (err: unknown) {
      setEditError(err instanceof Error ? err.message : "שגיאה בעדכון");
    } finally {
      setEditSaving(false);
    }
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    if (form.password !== form.password2) { setFormError("הסיסמאות אינן תואמות"); return; }
    setSaving(true);
    try {
      await createUser(form);
      const createdName = form.company_name || `${form.first_name} ${form.last_name}`;
      setShowModal(false);
      setForm(EMPTY_FORM);
      await load();
      setWhatsappLink({ name: createdName });
      setLinkCopied(false);
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "שגיאה ביצירת הלקוח");
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

  if (error) return <div className="flex items-center justify-center h-64"><p className="text-red-600">{error}</p></div>;
  if (!users) return <div className="flex items-center justify-center h-64"><p className="text-gray-500">טוען...</p></div>;

  return (
    <div className="px-6 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-800">ניהול לקוחות</h1>
        <button
          onClick={() => { setForm(EMPTY_FORM); setFormError(""); setShowModal(true); }}
          className="bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700 transition"
        >
          + הוסף לקוח
        </button>
      </div>

      <p className="text-sm text-gray-500 mb-4">{users.length} לקוחות רשומים</p>

      {users.length === 0 ? (
        <p className="text-gray-500 text-sm">אין לקוחות עדיין.</p>
      ) : (
        <div className="space-y-2">
          {users.map((u) => (
            <div key={u.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
              {/* Summary row */}
              <div className="flex items-center gap-2 px-4 py-3">
                <button
                  onClick={() => toggleExpand(u.id)}
                  className="flex-1 flex items-center gap-4 text-right hover:opacity-80 transition"
                >
                  <div className="flex-1 grid grid-cols-4 gap-4 text-sm min-w-0">
                    <span className="font-medium text-gray-800 truncate">{u.first_name} {u.last_name}</span>
                    <span className="text-gray-500 truncate">{u.email}</span>
                    <span className="text-gray-600 truncate">{u.profile?.company_name ?? "—"}</span>
                    <span className="text-gray-500 truncate">
                      {u.profile?.region ? (REGION_LABEL[u.profile.region] ?? u.profile.region) : "—"}
                    </span>
                  </div>
                  <span className={`text-gray-400 text-xs transition-transform shrink-0 ${expanded === u.id ? "rotate-180" : ""}`}>▼</span>
                </button>
                <button
                  onClick={() => openEdit(u)}
                  className="shrink-0 text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded-lg px-2 py-1 text-sm transition"
                  title="ערוך פרופיל"
                >
                  ✏️
                </button>
                <button
                  onClick={() => handleDelete(u.id, `${u.first_name} ${u.last_name}`)}
                  disabled={deletingId === u.id}
                  className="shrink-0 text-red-500 hover:text-red-700 hover:bg-red-50 rounded-lg px-2 py-1 text-sm transition disabled:opacity-40"
                  title="מחק לקוח"
                >
                  🗑
                </button>
              </div>

              {/* Expanded profile details */}
              {expanded === u.id && (
                <div className="border-t border-gray-100 px-4 py-4 bg-gray-50 grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
                  <Detail label="טלפון חברה" value={u.profile?.company_phone} />
                  <Detail label="כתובת" value={u.profile?.company_address} />
                  <Detail label="טלפון אישי" value={u.profile?.phone} />
                  <Detail label="תפקיד" value={u.profile?.position} />
                </div>
              )}
            </div>
          ))}
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
              שלח ללקוח את הקישור הבא כדי שיוכל להתחבר לבוט:
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

      {/* Edit profile modal */}
      {editUser && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <div>
                <h2 className="text-lg font-bold text-gray-800">עריכת פרופיל</h2>
                <p className="text-xs text-gray-500">{editUser.first_name} {editUser.last_name} · {editUser.email}</p>
              </div>
              <button onClick={() => setEditUser(null)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>
            <form onSubmit={handleEditSave} className="space-y-3">
              <Field label="שם חברה" name="company_name" value={editForm.company_name} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="טלפון חברה" name="company_phone" value={editForm.company_phone} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} />
                <Field label="מספר WhatsApp" name="phone" value={editForm.phone} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} placeholder="+972XXXXXXXXX" />
              </div>
              <Field label="כתובת למשלוח" name="company_address" value={editForm.company_address} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} />
              <Field label="תפקיד" name="position" value={editForm.position} onChange={(e) => setEditForm(p => ({ ...p, [e.target.name]: e.target.value }))} />
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">אזור</label>
                <select
                  name="region"
                  value={editForm.region}
                  onChange={(e) => setEditForm(p => ({ ...p, region: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {REGIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
              </div>
              {editError && <p className="text-red-600 text-sm">{editError}</p>}
              <div className="flex gap-3 pt-2">
                <button type="submit" disabled={editSaving} className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                  {editSaving ? "שומר..." : "שמור"}
                </button>
                <button type="button" onClick={() => setEditUser(null)} className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50">
                  ביטול
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

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
                <PasswordField label="סיסמה" name="password" value={form.password} onChange={handleChange} required />
                <PasswordField label="אימות סיסמה" name="password2" value={form.password2} onChange={handleChange} required />
              </div>
              <hr className="my-1" />
              <Field label="שם חברה" name="company_name" value={form.company_name} onChange={handleChange} required />
              <div className="grid grid-cols-2 gap-3">
                <Field label="טלפון חברה" name="company_phone" value={form.company_phone} onChange={handleChange} placeholder="+972XXXXXXXXX" />
                <Field label="מספר WhatsApp" name="phone" value={form.phone} onChange={handleChange} placeholder="+972XXXXXXXXX" />
              </div>
              <Field label="כתובת" name="company_address" value={form.company_address} onChange={handleChange} />
              <Field label="תפקיד" name="position" value={form.position} onChange={handleChange} />
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">אזור</label>
                <select name="region" value={form.region} onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500">
                  {REGIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
              </div>
              {formError && <p className="text-red-600 text-sm">{formError}</p>}
              <div className="flex gap-3 pt-2">
                <button type="submit" disabled={saving} className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                  {saving ? "שומר..." : "צור לקוח"}
                </button>
                <button type="button" onClick={() => setShowModal(false)} className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50">
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

function Detail({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <span className="text-xs text-gray-400">{label}: </span>
      <span className="text-gray-700">{value || "—"}</span>
    </div>
  );
}

function Field({
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
        type={type} name={name} value={value} onChange={onChange} required={required}
        placeholder={placeholder}
        autoComplete={type === "email" ? "email" : "off"}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}

function PasswordField({
  label, name, value, onChange, required = false,
}: {
  label: string; name: string; value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  required?: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <div className="relative">
        <input
          type={show ? "text" : "password"} name={name} value={value} onChange={onChange}
          required={required} autoComplete="new-password"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 pl-9"
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-base leading-none"
          tabIndex={-1}
        >
          {show ? "🙈" : "👁"}
        </button>
      </div>
    </div>
  );
}
