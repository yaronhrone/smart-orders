"use client";

import { useEffect, useState } from "react";
import { fetchMe, updateProfile, ProfilePayload } from "../../lib/api";

const REGIONS = [
  { value: "tel_aviv", label: "תל אביב" },
  { value: "jerusalem", label: "ירושלים" },
  { value: "haifa", label: "חיפה" },
  { value: "south", label: "דרום" },
  { value: "north", label: "צפון" },
  { value: "center", label: "מרכז" },
];

const EMPTY: ProfilePayload = {
  company_name: "",
  company_phone: "",
  company_address: "",
  phone: "",
  position: "",
  region: "center",
};

export default function ProfilePage() {
  const [form, setForm] = useState<ProfilePayload>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchMe()
      .then((me) => {
        if (me.profile) {
          setForm({
            company_name: me.profile.company_name ?? "",
            company_phone: me.profile.company_phone ?? "",
            company_address: me.profile.company_address ?? "",
            phone: me.profile.phone ?? "",
            position: me.profile.position ?? "",
            region: me.profile.region ?? "center",
          });
        }
      })
      .catch(() => setError("שגיאה בטעינת הפרופיל"))
      .finally(() => setLoading(false));
  }, []);

  function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
    setSuccess(false);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await updateProfile(form);
      setSuccess(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "שגיאה בשמירה");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6 max-w-lg">
      <h1 className="text-xl font-bold text-gray-800 mb-6">פרופיל חברה</h1>

      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-sm p-6 space-y-4" dir="rtl">
        <Field label="שם חברה / מסעדה" name="company_name" value={form.company_name ?? ""} onChange={handleChange} />
        <Field label="טלפון חברה" name="company_phone" value={form.company_phone ?? ""} onChange={handleChange} />
        <Field label="כתובת למשלוח" name="company_address" value={form.company_address ?? ""} onChange={handleChange} placeholder="רחוב, מספר, עיר" />
        <Field label="טלפון אישי (WhatsApp לקבלת אישורים)" name="phone" value={form.phone ?? ""} onChange={handleChange} />
        <Field label="תפקיד" name="position" value={form.position ?? ""} onChange={handleChange} />

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

        {error && <p className="text-red-600 text-sm">{error}</p>}
        {success && <p className="text-green-600 text-sm">✅ הפרופיל עודכן בהצלחה</p>}

        <button
          type="submit"
          disabled={saving}
          className="w-full bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 mt-2"
        >
          {saving ? "שומר..." : "שמור שינויים"}
        </button>
      </form>
    </div>
  );
}

function Field({
  label,
  name,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type="text"
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}
