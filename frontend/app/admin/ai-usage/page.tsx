"use client";

import { useEffect, useState } from "react";
import { useAutoError } from "../../lib/useAutoError";
import { fetchAIUsage, AIUsage, AIUsageRow } from "../../lib/api";

const KIND_LABEL: Record<string, string> = {
  order_parse: "פענוח הזמנה",
  modification_parse: "פענוח שינוי בהזמנה",
  price_parse: "פענוח מחירון ספק",
  assistant: "העוזר (צ'אט)",
  other: "אחר",
};

const RANGES = [
  { days: 1, label: "היום" },
  { days: 7, label: "7 ימים" },
  { days: 30, label: "30 יום" },
  { days: 365, label: "שנה" },
];

const usd = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
const num = (n: number) => n.toLocaleString("he-IL");

function Table({ title, rows, labelOf }: { title: string; rows: AIUsageRow[]; labelOf: (r: AIUsageRow) => string }) {
  return (
    <section className="bg-white rounded-lg shadow p-4 mb-6 overflow-x-auto">
      <h2 className="font-semibold mb-3">{title}</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-gray-500">אין נתונים בתקופה הזו.</p>
      ) : (
        <table className="w-full text-sm text-right">
          <thead>
            <tr className="text-gray-500 border-b">
              <th className="py-2 pr-2"></th>
              <th>קריאות</th>
              <th>נכשלו</th>
              <th>טוקנים (קלט / פלט)</th>
              <th>עלות משוערת</th>
              <th>עלות לקריאה</th>
              <th>זמן ממוצע</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b last:border-0">
                <td className="py-2 pr-2 font-medium">{labelOf(r)}</td>
                <td>{num(r.calls)}</td>
                <td className={r.failed ? "text-red-600" : ""}>{num(r.failed)}</td>
                <td>
                  {num(r.input_tokens)} / {num(r.output_tokens)}
                </td>
                <td>{usd(r.cost_usd)}</td>
                <td>{usd(r.avg_cost_per_call_usd)}</td>
                <td>{r.avg_seconds} ש׳</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function AIUsagePage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<AIUsage | null>(null);
  const [error, setError] = useAutoError();

  useEffect(() => {
    setData(null);
    fetchAIUsage(days)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "שגיאה בטעינת הנתונים"));
    // setError is a new function on every render; listing it here would reload the page in a loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const parsing = data ? Object.entries(data.parsing_today) : [];

  return (
    <div className="p-4 md:p-8 max-w-5xl">
      <h1 className="text-2xl font-bold mb-1">שימוש ב-AI</h1>
      <p className="text-sm text-gray-500 mb-4">
        העלויות הן הערכה לפי מחירון בקוד, לא חשבונית. המודל הפעיל עכשיו: {data?.current_model ?? "…"}
      </p>

      <div className="flex gap-2 mb-6">
        {RANGES.map((r) => (
          <button
            key={r.days}
            onClick={() => setDays(r.days)}
            className={`px-3 py-1 rounded border text-sm ${days === r.days ? "bg-blue-600 text-white" : "bg-white"}`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {error && <p className="text-red-600 mb-4">{error}</p>}
      {!data && !error && <p className="text-gray-500">טוען…</p>}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            {[
              ["קריאות ל-AI", num(data.totals.calls)],
              ["נכשלו", num(data.totals.failed)],
              ["טוקנים", `${num(data.totals.input_tokens + data.totals.output_tokens)}`],
              ["עלות משוערת", usd(data.totals.cost_usd)],
            ].map(([label, value]) => (
              <div key={label} className="bg-white rounded-lg shadow p-4">
                <div className="text-xs text-gray-500">{label}</div>
                <div className="text-xl font-bold">{value}</div>
              </div>
            ))}
          </div>

          <section className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="font-semibold mb-1">היום: מילון מול AI</h2>
            <p className="text-xs text-gray-500 mb-2">הודעה שהמילון הבין עולה אפס. כל הודעה שעברה ל-AI עולה כסף.</p>
            {parsing.length === 0 ? (
              <p className="text-sm text-gray-500">עוד לא היו הודעות היום.</p>
            ) : (
              <ul className="text-sm">
                {parsing.map(([kind, c]) => (
                  <li key={kind}>
                    {KIND_LABEL[kind] ?? kind}: מילון {c.dictionary} · AI {c.ai}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="font-semibold mb-1">מה ה-AI קיבל ומה הוא הבין</h2>
            <p className="text-xs text-gray-500 mb-3">
              50 הקריאות האחרונות של הפענוח (לא של העוזר). מופיע רק הטקסט שהמילון לא הצליח לקרוא בעצמו, וטקסט
              ותוצאה נמחקים אוטומטית אחרי 24 שעות (הסטטיסטיקה והעלויות נשארות). מוצר שחוזר כאן הרבה פעמים כדאי
              להכניס למילון.
            </p>
            {data.recent.length === 0 ? (
              <p className="text-sm text-gray-500">אין קריאות בתקופה הזו.</p>
            ) : (
              <ul className="space-y-2">
                {data.recent.map((r, i) => (
                  <li key={i} className="border rounded p-2 text-sm">
                    <div className="flex flex-wrap gap-x-3 text-xs text-gray-500 mb-1">
                      <span>{new Date(r.at).toLocaleString("he-IL")}</span>
                      <span>{KIND_LABEL[r.kind] ?? r.kind}</span>
                      <span>{r.model}</span>
                      <span>{r.seconds} ש׳</span>
                      <span>{usd(r.cost_usd)}</span>
                      {!r.ok && <span className="text-red-600">נכשלה</span>}
                    </div>
                    <div>
                      <span className="text-gray-500">קיבל: </span>
                      {r.message || "—"}
                    </div>
                    <div className="break-all" dir="ltr" style={{ textAlign: "left" }}>
                      <span className="text-gray-500" dir="rtl">הבין: </span>
                      {r.result || "—"}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Table title="השוואה בין מודלים" rows={data.by_model} labelOf={(r) => r.model ?? ""} />
          <Table
            title="מודל לפי סוג קריאה"
            rows={data.by_model_and_kind}
            labelOf={(r) => `${r.model} · ${KIND_LABEL[r.kind ?? ""] ?? r.kind}`}
          />
          <Table title="לפי סוג קריאה" rows={data.by_kind} labelOf={(r) => KIND_LABEL[r.kind ?? ""] ?? r.kind ?? ""} />
          <Table title="לפי יום" rows={[...data.by_day].reverse()} labelOf={(r) => r.day ?? ""} />
        </>
      )}
    </div>
  );
}
