"use client";

import { useEffect, useState } from "react";
import {
  ConnectionToken,
  createConnection,
  fetchConnections,
  revokeConnection,
} from "../../lib/api";

// Where Claude reaches the MCP server: the same domain in production, the
// local container when the site itself runs on localhost.
function mcpUrl(): string {
  if (typeof window === "undefined") return "";
  const { hostname, origin } = window.location;
  return hostname === "localhost" || hostname === "127.0.0.1" ? "http://localhost:8765/mcp" : `${origin}/mcp`;
}

function desktopConfig(url: string, token: string): string {
  const args = [
    "-y",
    "mcp-remote",
    url,
    ...(url.startsWith("http://") ? ["--allow-http"] : []),
    "--header",
    "Authorization:${AUTH}",
  ];
  return JSON.stringify(
    { mcpServers: { "smart-orders": { command: "npx", args, env: { AUTH: `Bearer ${token}` } } } },
    null,
    2
  );
}

function codeCommand(url: string, token: string): string {
  return `claude mcp add --transport http smart-orders ${url} --header "Authorization: Bearer ${token}"`;
}

function fmt(date: string | null): string {
  return date ? new Date(date).toLocaleDateString("he-IL") : "—";
}

export default function ConnectPage() {
  const [tokens, setTokens] = useState<ConnectionToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const [tab, setTab] = useState<"desktop" | "code">("desktop");
  const [copied, setCopied] = useState(false);
  const url = mcpUrl();

  async function load() {
    try {
      setTokens(await fetchConnections());
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינה");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const created = await createConnection(name.trim());
      setFresh(created.token);
      setName("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה ביצירת החיבור");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    if (!confirm("לבטל את החיבור? Claude שמשתמש בו יפסיק לעבוד.")) return;
    try {
      await revokeConnection(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בביטול");
    }
  }

  const snippet = fresh ? (tab === "desktop" ? desktopConfig(url, fresh) : codeCommand(url, fresh)) : "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("ההעתקה נכשלה, סמן והעתק ידנית");
    }
  }

  return (
    <div className="px-6 py-6 max-w-2xl" dir="rtl">
      <h1 className="text-xl font-bold text-gray-800 mb-2">חיבור ל-Claude</h1>
      <p className="text-sm text-gray-600 mb-6">
        אחרי החיבור אפשר לכתוב ל-Claude, למשל &quot;אני רוצה 30 ק&quot;ג עגבנייה&quot;. הוא יציג הצעה עם הספק והמחיר,
        וההזמנה תישלח רק אחרי שתאשר. Claude רואה ויוצר הזמנות רק של החברה שלך.
      </p>

      {error && <p className="mb-4 rounded-lg bg-red-50 text-red-700 text-sm px-3 py-2">{error}</p>}

      <section className="bg-white rounded-2xl shadow-sm p-6 mb-6">
        <h2 className="font-semibold text-gray-800 mb-3">1. צור חיבור</h2>
        <div className="flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            placeholder="שם לחיבור, למשל: המחשב במשרד"
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm"
          />
          <button
            onClick={create}
            disabled={busy}
            className="bg-green-700 text-white text-sm px-4 py-2 rounded-lg disabled:opacity-50"
          >
            {busy ? "יוצר..." : "צור חיבור"}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-2">תקף 90 יום. עד 3 חיבורים פעילים.</p>
      </section>

      {fresh && (
        <section className="bg-amber-50 border border-amber-200 rounded-2xl p-6 mb-6">
          <h2 className="font-semibold text-gray-800 mb-1">2. חבר ל-Claude</h2>
          <p className="text-sm text-amber-800 mb-3">
            החיבור מוצג פעם אחת בלבד. אם תסגור את הדף, יהיה צריך ליצור חיבור חדש.
          </p>
          <div className="flex gap-2 mb-3 text-sm">
            <button
              onClick={() => setTab("desktop")}
              className={`px-3 py-1 rounded-lg ${tab === "desktop" ? "bg-green-700 text-white" : "bg-white border"}`}
            >
              Claude Desktop
            </button>
            <button
              onClick={() => setTab("code")}
              className={`px-3 py-1 rounded-lg ${tab === "code" ? "bg-green-700 text-white" : "bg-white border"}`}
            >
              Claude Code
            </button>
          </div>

          {tab === "desktop" ? (
            <ol className="text-sm text-gray-700 list-decimal pr-5 space-y-1 mb-3">
              <li>צריך Node.js מותקן (nodejs.org).</li>
              <li>ב-Claude Desktop: Settings ← Developer ← Edit Config.</li>
              <li>הדבק את התוכן למטה בקובץ שנפתח ושמור.</li>
              <li>סגור את Claude Desktop לגמרי ופתח מחדש.</li>
            </ol>
          ) : (
            <p className="text-sm text-gray-700 mb-3">הרץ בטרמינל, ואז הפעל מחדש את Claude Code.</p>
          )}

          <pre dir="ltr" className="bg-gray-900 text-green-200 text-xs rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
            {snippet}
          </pre>
          <button onClick={copy} className="mt-2 bg-green-700 text-white text-sm px-4 py-2 rounded-lg">
            {copied ? "הועתק" : "העתק"}
          </button>
        </section>
      )}

      <section className="bg-white rounded-2xl shadow-sm p-6">
        <h2 className="font-semibold text-gray-800 mb-3">החיבורים שלי</h2>
        {loading ? (
          <p className="text-sm text-gray-500">טוען...</p>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-gray-500">אין חיבורים פעילים.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {tokens.map((t) => (
              <li key={t.id} className="flex items-center justify-between py-3 text-sm">
                <div>
                  <p className="font-medium text-gray-800">{t.name || "ללא שם"}</p>
                  <p className="text-xs text-gray-500">
                    נוצר {fmt(t.created_at)} · שימוש אחרון {fmt(t.last_used_at)} · תוקף עד {fmt(t.expires_at)}
                  </p>
                </div>
                <button onClick={() => revoke(t.id)} className="text-red-600 hover:underline">
                  בטל
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
