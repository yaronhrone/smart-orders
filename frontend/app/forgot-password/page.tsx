"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { requestPasswordReset } from "../lib/api";
import { useAutoError } from "../lib/useAutoError";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useAutoError(5000);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : "לא הצלחנו לשלוח, נסו שוב");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-sm" dir="rtl">
        <h1 className="text-2xl font-bold text-green-900 text-center mb-2">שכחתי סיסמה</h1>
        {sent ? (
          <p className="text-sm text-gray-600 text-center mt-4">
            אם האימייל רשום במערכת, נשלח אליו קישור לבחירת סיסמה חדשה. כדאי לבדוק גם בתיקיית הספאם.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 mt-4">
            <p className="text-sm text-gray-600">נשלח קישור לבחירת סיסמה חדשה לאימייל שלך.</p>
            <div>
              <label className="block text-sm font-semibold text-green-800 mb-1">אימייל</label>
              <input type="email" value={email} required autoComplete="email"
                onChange={(e) => { setEmail(e.target.value); setError(""); }}
                className="w-full border-2 border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 focus:outline-none focus:border-green-500" />
            </div>
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" disabled={loading}
              className="w-full bg-green-700 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-green-800 disabled:opacity-50 transition">
              {loading ? "שולח..." : "שלחו לי קישור"}
            </button>
          </form>
        )}
        <p className="text-center text-sm text-gray-500 mt-4">
          <Link href="/login" className="text-green-700 font-semibold">חזרה לכניסה</Link>
        </p>
      </div>
    </main>
  );
}
