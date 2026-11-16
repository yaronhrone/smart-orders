"use client";

import { useState, FormEvent } from "react";
import { changePassword } from "../lib/api";

const INPUT =
  "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";

export function ChangePasswordCard() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess(false);
    if (newPassword !== newPassword2) {
      setError("הסיסמאות החדשות אינן תואמות");
      return;
    }
    setSaving(true);
    try {
      await changePassword(oldPassword, newPassword, newPassword2);
      setOldPassword("");
      setNewPassword("");
      setNewPassword2("");
      setSuccess(true);
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : "שגיאה בהחלפת הסיסמה");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-sm p-6 space-y-4 mt-6" dir="rtl">
      <h2 className="text-base font-bold text-gray-800">החלפת סיסמה</h2>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">סיסמה נוכחית</label>
        <input type="password" className={INPUT} value={oldPassword} required autoComplete="current-password"
          onChange={(e) => { setOldPassword(e.target.value); setSuccess(false); }} />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">סיסמה חדשה</label>
        <input type="password" className={INPUT} value={newPassword} required autoComplete="new-password"
          onChange={(e) => { setNewPassword(e.target.value); setSuccess(false); }} />
        <p className="text-xs text-gray-400 mt-1">לפחות 8 תווים, אות גדולה אחת וסימן מיוחד (!@#$).</p>
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">אימות סיסמה חדשה</label>
        <input type="password" className={INPUT} value={newPassword2} required autoComplete="new-password"
          onChange={(e) => { setNewPassword2(e.target.value); setSuccess(false); }} />
      </div>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      {success && <p className="text-green-600 text-sm">✅ הסיסמה הוחלפה</p>}
      <button type="submit" disabled={saving}
        className="w-full bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
        {saving ? "שומר..." : "החלף סיסמה"}
      </button>
    </form>
  );
}
