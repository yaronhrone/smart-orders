"use client";

import { useEffect, useState, FormEvent } from "react";
import { PasswordInput } from "../components/PasswordInput";
import Link from "next/link";
import { confirmPasswordReset } from "../lib/api";
import { useAutoError } from "../lib/useAutoError";

const INPUT =
  "w-full border-2 border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:border-green-500";

export default function ResetPasswordPage() {
  const [uid, setUid] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useAutoError(8000);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  // The link's query string, read in the browser (same approach as the login page).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setUid(params.get("uid") ?? "");
    setToken(params.get("token") ?? "");
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== password2) {
      setError("הסיסמאות אינן תואמות");
      return;
    }
    setLoading(true);
    try {
      await confirmPasswordReset(uid, token, password, password2);
      setDone(true);
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : "לא הצלחנו להחליף את הסיסמה");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-sm" dir="rtl">
        <h1 className="text-2xl font-bold text-green-900 text-center mb-2">סיסמה חדשה</h1>
        {done ? (
          <div className="text-center mt-4">
            <p className="text-sm text-gray-600 mb-4">הסיסמה הוחלפה.</p>
            <Link href="/login" className="inline-block bg-green-700 text-white rounded-lg px-6 py-2.5 text-sm font-semibold hover:bg-green-800">
              כניסה
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 mt-4">
            <div>
              <label className="block text-sm font-semibold text-green-800 mb-1">סיסמה חדשה</label>
              <PasswordInput className={INPUT} value={password} autoComplete="new-password"
                onChange={(e) => { setPassword(e.target.value); setError(""); }} />
              <p className="text-xs text-gray-400 mt-1">לפחות 8 תווים, אות גדולה אחת וסימן מיוחד (!@#$).</p>
            </div>
            <div>
              <label className="block text-sm font-semibold text-green-800 mb-1">אימות סיסמה</label>
              <PasswordInput className={INPUT} value={password2} autoComplete="new-password"
                onChange={(e) => { setPassword2(e.target.value); setError(""); }} />
            </div>
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" disabled={loading || !uid || !token}
              className="w-full bg-green-700 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-800 disabled:opacity-50 transition">
              {loading ? "שומר..." : "שמירת סיסמה"}
            </button>
            <p className="text-center text-sm text-gray-500">
              הקישור לא עובד? <Link href="/forgot-password" className="text-green-700 font-semibold">בקשו קישור חדש</Link>
            </p>
          </form>
        )}
      </div>
    </main>
  );
}
