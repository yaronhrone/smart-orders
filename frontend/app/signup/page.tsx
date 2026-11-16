"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { submitSignup } from "../lib/api";
import { useAutoError } from "../lib/useAutoError";

const REGIONS = [
  { value: "tel_aviv", label: "תל אביב" },
  { value: "jerusalem", label: "ירושלים" },
  { value: "haifa", label: "חיפה" },
  { value: "south", label: "דרום" },
  { value: "north", label: "צפון" },
  { value: "center", label: "מרכז" },
];

const MAX_SUPPLIERS = 10;
const INPUT =
  "w-full border-2 border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:border-green-500";
const LABEL = "block text-sm font-semibold text-green-800 mb-1";

type Supplier = { name: string; phone: string };

export default function SignupPage() {
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    company_name: "",
    company_address: "",
    region: "tel_aviv",
  });
  const [suppliers, setSuppliers] = useState<Supplier[]>([{ name: "", phone: "" }]);
  const [website, setWebsite] = useState(""); // honeypot
  const [error, setError] = useAutoError(8000);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  function setField(key: keyof typeof form, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    setError("");
  }

  function setSupplier(i: number, key: keyof Supplier, value: string) {
    setSuppliers((list) => list.map((s, idx) => (idx === i ? { ...s, [key]: value } : s)));
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    // A half-filled supplier row is a mistake worth showing; a fully empty one is just skipped.
    const filled = suppliers.filter((s) => s.name.trim() || s.phone.trim());
    if (filled.some((s) => !s.name.trim() || !s.phone.trim())) {
      setError("בכל ספק יש למלא גם שם וגם טלפון");
      return;
    }
    setLoading(true);
    try {
      await submitSignup({ ...form, suppliers: filled, website });
      setDone(true);
    } catch (e: unknown) {
      setError(e instanceof Error && e.message ? e.message : "לא הצלחנו לשלוח, נסו שוב");
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
        <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md text-center" dir="rtl">
          <div className="text-4xl mb-3">✅</div>
          <h1 className="text-2xl font-bold text-green-900 mb-2">קיבלנו את הבקשה</h1>
          <p className="text-sm text-gray-600">
            ניצור איתך קשר בקרוב. אחרי שנסיים להתחבר לספקים שלך תקבל אישור במייל ובוואטסאפ.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-lg" dir="rtl">
        <div className="text-center mb-6">
          <div className="text-4xl mb-3">🥦🍅🧅</div>
          <h1 className="text-2xl font-bold text-green-900">הרשמה</h1>
          <p className="text-sm text-green-600 mt-1 font-medium">Smart Orders</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL}>שם פרטי</label>
              <input className={INPUT} required maxLength={100} value={form.first_name}
                onChange={(e) => setField("first_name", e.target.value)} autoComplete="given-name" />
            </div>
            <div>
              <label className={LABEL}>שם משפחה</label>
              <input className={INPUT} required maxLength={100} value={form.last_name}
                onChange={(e) => setField("last_name", e.target.value)} autoComplete="family-name" />
            </div>
          </div>
          <div>
            <label className={LABEL}>אימייל</label>
            <input type="email" className={INPUT} required value={form.email}
              onChange={(e) => setField("email", e.target.value)} autoComplete="email" />
          </div>
          <div>
            <label className={LABEL}>טלפון (לתקשורת ולוואטסאפ)</label>
            <input type="tel" className={INPUT} required value={form.phone}
              onChange={(e) => setField("phone", e.target.value)} autoComplete="tel" />
          </div>
          <div>
            <label className={LABEL}>שם העסק</label>
            <input className={INPUT} required maxLength={150} value={form.company_name}
              onChange={(e) => setField("company_name", e.target.value)} autoComplete="organization" />
          </div>
          <div>
            <label className={LABEL}>כתובת העסק</label>
            <input className={INPUT} maxLength={250} value={form.company_address}
              onChange={(e) => setField("company_address", e.target.value)} autoComplete="street-address" />
          </div>
          <div>
            <label className={LABEL}>אזור</label>
            <select className={INPUT} value={form.region} onChange={(e) => setField("region", e.target.value)}>
              {REGIONS.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>

          <div className="border-t pt-4">
            <h2 className="text-sm font-bold text-green-900 mb-1">הספקים שאתם עובדים איתם</h2>
            <p className="text-xs text-gray-500 mb-3">נתקשר אליהם ונחבר אותם למערכת.</p>
            <div className="space-y-2">
              {suppliers.map((s, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <input className={INPUT} placeholder="שם הספק" maxLength={100} value={s.name}
                    onChange={(e) => setSupplier(i, "name", e.target.value)} />
                  <input type="tel" className={INPUT} placeholder="טלפון" maxLength={20} value={s.phone}
                    onChange={(e) => setSupplier(i, "phone", e.target.value)} />
                  {suppliers.length > 1 && (
                    <button type="button" aria-label="הסר ספק"
                      onClick={() => setSuppliers((l) => l.filter((_, idx) => idx !== i))}
                      className="text-gray-400 hover:text-red-600 text-lg px-1">✕</button>
                  )}
                </div>
              ))}
            </div>
            {suppliers.length < MAX_SUPPLIERS && (
              <button type="button" onClick={() => setSuppliers((l) => [...l, { name: "", phone: "" }])}
                className="mt-2 text-sm font-semibold text-green-700 hover:text-green-900">
                + ספק נוסף
              </button>
            )}
          </div>

          {/* Honeypot: invisible to people, bots fill it in. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
            <label>אתר אינטרנט
              <input tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
            </label>
          </div>

          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full bg-green-700 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-800 disabled:opacity-50 transition">
            {loading ? "שולח..." : "שליחת בקשה"}
          </button>
          <p className="text-center text-sm text-gray-500">
            כבר רשומים? <Link href="/login" className="text-green-700 font-semibold">כניסה</Link>
          </p>
        </form>
      </div>
    </main>
  );
}
