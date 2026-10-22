"use client";

import { useEffect, useState } from "react";

// The approval step of connecting Claude. Claude sends the customer's browser here
// (the "authorization endpoint"); after "approve" the browser goes back to Claude
// with a one-time code. Everything is checked again on the server.

interface AuthParams {
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  code_challenge_method: string;
  state: string;
}

async function signedIn(): Promise<boolean> {
  const me = await fetch("/api/users/me/", { credentials: "include" });
  if (me.ok) return true;
  const refreshed = await fetch("/api/users/token/refresh/", { method: "POST", credentials: "include" });
  return refreshed.ok;
}

function readParams(): AuthParams {
  const q = new URLSearchParams(window.location.search);
  return {
    client_id: q.get("client_id") ?? "",
    redirect_uri: q.get("redirect_uri") ?? "",
    code_challenge: q.get("code_challenge") ?? "",
    code_challenge_method: q.get("code_challenge_method") ?? "",
    state: q.get("state") ?? "",
  };
}

function errorText(body: string): string {
  try {
    const parsed = JSON.parse(body);
    return parsed.error_description ?? parsed.detail ?? "אירעה שגיאה";
  } catch {
    return "אירעה שגיאה";
  }
}

export default function AuthorizePage() {
  const [params, setParams] = useState<AuthParams | null>(null);
  const [clientName, setClientName] = useState("");
  const [company, setCompany] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      const p = readParams();
      if (!p.client_id || !p.redirect_uri || !p.code_challenge || p.code_challenge_method !== "S256") {
        setError("הבקשה לחיבור אינה תקינה. התחל שוב מתוך Claude.");
        return;
      }
      if (!(await signedIn())) {
        const back = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/login?next=${back}`;
        return;
      }
      const info = await fetch(
        `/api/oauth/authorize/?client_id=${encodeURIComponent(p.client_id)}&redirect_uri=${encodeURIComponent(p.redirect_uri)}`,
        { credentials: "include" }
      );
      if (!info.ok) {
        setError(errorText(await info.text()));
        return;
      }
      setClientName((await info.json()).client_name);
      const me = await fetch("/api/users/me/", { credentials: "include" });
      if (me.ok) {
        const data = await me.json();
        setCompany(data.profile?.company_name || data.email || "");
      }
      setParams(p);
    })().catch(() => setError("אירעה שגיאה בטעינה"));
  }, []);

  async function approve() {
    if (!params) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/oauth/authorize/", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        setError(errorText(await res.text()));
        return;
      }
      window.location.href = (await res.json()).redirect_to;
    } catch {
      setError("אירעה שגיאה, נסה שוב");
    } finally {
      setBusy(false);
    }
  }

  function deny() {
    if (!params) return;
    const sep = params.redirect_uri.includes("?") ? "&" : "?";
    const state = params.state ? `&state=${encodeURIComponent(params.state)}` : "";
    window.location.href = `${params.redirect_uri}${sep}error=access_denied${state}`;
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md" dir="rtl">
        <h1 className="text-xl font-bold text-green-900 mb-4">חיבור ל-Smart Orders</h1>
        {error && <p className="rounded-lg bg-red-50 text-red-700 text-sm px-3 py-2 mb-4">{error}</p>}
        {params ? (
          <>
            <p className="text-sm text-gray-700 mb-3">
              <strong>{clientName}</strong> מבקש לפעול בשם <strong>{company}</strong>.
            </p>
            <ul className="text-sm text-gray-600 list-disc pr-5 space-y-1 mb-5">
              <li>לראות מוצרים, מחירים והזמנות של החברה שלך.</li>
              <li>להכין הצעת הזמנה. הזמנה נשלחת לספקים רק אחרי שאתה מאשר אותה בשיחה.</li>
              <li>אין גישה ללקוחות אחרים.</li>
            </ul>
            <p className="text-xs text-gray-400 mb-5">אפשר לבטל את החיבור בכל רגע בעמוד &quot;חיבור ל-Claude&quot;.</p>
            <div className="flex gap-2">
              <button
                onClick={approve}
                disabled={busy}
                className="flex-1 bg-green-700 text-white text-sm py-2 rounded-lg disabled:opacity-50"
              >
                {busy ? "מחבר..." : "אשר חיבור"}
              </button>
              <button onClick={deny} className="flex-1 border border-gray-300 text-gray-700 text-sm py-2 rounded-lg">
                ביטול
              </button>
            </div>
          </>
        ) : (
          !error && <p className="text-sm text-gray-500">טוען...</p>
        )}
      </div>
    </main>
  );
}
