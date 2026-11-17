"use client";

import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { changePassword, fetchMe, logout } from "../lib/api";
import { useAutoError } from "../lib/useAutoError";

const INPUT =
  "w-full border-2 border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:border-green-500";

// First login with a password the admin/system chose: pick your own, then sign in again.
export default function ChangePasswordPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useAutoError(8000);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchMe()
      .then((me) => {
        if (!me.must_change_password) router.replace("/dashboard");
        else setReady(true);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== password2) {
      setError("הסיסמאות אינן תואמות");
      return;
    }
    setLoading(true);
    try {
      await changePassword("", password, password2);
      await logout().catch(() => {});
      router.replace("/login");
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : "לא הצלחנו להחליף את הסיסמה");
      setLoading(false);
    }
  }

  if (!ready) {
    return <main className="min-h-screen flex items-center justify-center bg-gray-100"><p className="text-gray-500">טוען...</p></main>;
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-sm" dir="rtl">
        <h1 className="text-2xl font-bold text-green-900 text-center mb-2">בחירת סיסמה חדשה</h1>
        <p className="text-sm text-gray-600 text-center">הסיסמה שקיבלת זמנית. בחרו סיסמה משלכם, ואז תיכנסו מחדש.</p>
        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div>
            <label className="block text-sm font-semibold text-green-800 mb-1">סיסמה חדשה</label>
            <input type="password" className={INPUT} value={password} required autoComplete="new-password"
              onChange={(e) => { setPassword(e.target.value); setError(""); }} />
            <p className="text-xs text-gray-400 mt-1">לפחות 8 תווים, אות גדולה אחת וסימן מיוחד (!@#$).</p>
          </div>
          <div>
            <label className="block text-sm font-semibold text-green-800 mb-1">אימות סיסמה</label>
            <input type="password" className={INPUT} value={password2} required autoComplete="new-password"
              onChange={(e) => { setPassword2(e.target.value); setError(""); }} />
          </div>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full bg-green-700 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-800 disabled:opacity-50 transition">
            {loading ? "שומר..." : "שמירה וכניסה מחדש"}
          </button>
        </form>
      </div>
    </main>
  );
}
