"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchMe,
  fetchProducts,
  suggestOrder,
  placeOrder,
  Product,
  SuggestOrderResponse,
  PlaceOrderResponse,
} from "../../lib/api";

function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatQty(qty: string | number): string {
  const n = Number(qty);
  return n % 1 === 0 ? String(Math.round(n)) : n.toFixed(1);
}

const WHOLE_UNITS = new Set(["UNIT", "BOX"]);

type Step = 1 | 2 | 3;

interface OrderItem {
  name: string;
  quantity: string;
}

export default function NewOrderPage() {
  const router = useRouter();

  const [step, setStep] = useState<Step>(1);
  const [region, setRegion] = useState("center");
  const [catalog, setCatalog] = useState<Product[]>([]);

  // Step 1
  const [items, setItems] = useState<OrderItem[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const saved = localStorage.getItem("new-order-items");
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [search, setSearch] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  // Step 2
  const [suggestion, setSuggestion] = useState<SuggestOrderResponse | null>(null);
  const [loadingSuggest, setLoadingSuggest] = useState(false);
  const [suggestError, setSuggestError] = useState("");

  // Step 3
  const [placed, setPlaced] = useState<PlaceOrderResponse | null>(null);
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState("");

  useEffect(() => {
    fetchMe()
      .then((me) => { if (me.profile?.region) setRegion(me.profile.region); })
      .catch(() => {});
    fetchProducts().then(setCatalog).catch(() => {});
  }, []);

  useEffect(() => {
    localStorage.setItem("new-order-items", JSON.stringify(items));
  }, [items]);

  // ─── Step 1 helpers ────────────────────────────────────────────────────

  const filtered = catalog
    .filter(
      (p) =>
        p.name.includes(search) &&
        !items.some((i) => i.name === p.name)
    )
    .slice(0, 8);

  function selectProduct(name: string) {
    setItems((prev) => [...prev, { name, quantity: "1" }]);
    setSearch("");
    setDropdownOpen(false);
    searchRef.current?.focus();
  }

  function removeItem(idx: number) {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateQty(idx: number, qty: string) {
    setItems((prev) => prev.map((item, i) => (i === idx ? { ...item, quantity: qty } : item)));
  }

  // ─── Step 2 ────────────────────────────────────────────────────────────

  async function handleSuggest() {
    if (items.length === 0) return;
    setLoadingSuggest(true);
    setSuggestError("");
    try {
      const result = await suggestOrder(
        items.map((i) => ({ product_name: i.name, quantity: i.quantity }))
      );
      setSuggestion(result);
      setStep(2);
    } catch (e: unknown) {
      setSuggestError(e instanceof Error ? e.message : "שגיאה בקבלת הצעות מחיר");
    } finally {
      setLoadingSuggest(false);
    }
  }

  // ─── Step 3 ────────────────────────────────────────────────────────────

  async function handlePlace(scenario: "cheapest" | "fewest_suppliers") {
    setPlacing(true);
    setPlaceError("");
    try {
      const result = await placeOrder(
        items.map((i) => ({ product_name: i.name, quantity: i.quantity })),
        scenario,
        region
      );
      setPlaced(result);
      setStep(3);
    } catch (e: unknown) {
      setPlaceError(e instanceof Error ? e.message : "שגיאה בביצוע ההזמנה");
    } finally {
      setPlacing(false);
    }
  }

  // ─── Render ────────────────────────────────────────────────────────────

  return (
    <div className="px-6 py-6 max-w-2xl">
      <h1 className="text-xl font-bold text-gray-800 mb-6">הזמנה חדשה</h1>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-8">
        {([1, 2, 3] as Step[]).map((s) => (
          <div key={s} className="flex items-center gap-2">
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${
                step === s
                  ? "bg-blue-600 text-white"
                  : step > s
                  ? "bg-green-500 text-white"
                  : "bg-gray-200 text-gray-500"
              }`}
            >
              {s}
            </div>
            {s < 3 && <div className={`flex-1 h-0.5 w-8 ${step > s ? "bg-green-400" : "bg-gray-200"}`} />}
          </div>
        ))}
        <span className="text-sm text-gray-500 mr-2">
          {step === 1 ? "בחר מוצרים" : step === 2 ? "בחר הצעה" : "הזמנה בוצעה"}
        </span>
      </div>

      {/* ─── Step 1: Products ─────────────────────────────────────────── */}
      {step === 1 && (
        <div className="space-y-5">
          {/* Search */}
          <div className="relative">
            <label className="block text-sm font-medium text-gray-700 mb-1">הוסף מוצר</label>
            <input
              ref={searchRef}
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setDropdownOpen(true); }}
              onFocus={() => setDropdownOpen(true)}
              onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
              placeholder="הקלד שם מוצר..."
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {dropdownOpen && search && filtered.length > 0 && (
              <ul className="absolute right-0 left-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-10 max-h-48 overflow-y-auto">
                {filtered.map((p) => (
                  <li
                    key={p.id}
                    onMouseDown={() => selectProduct(p.name)}
                    className="px-4 py-2.5 text-sm text-gray-700 hover:bg-blue-50 cursor-pointer flex justify-between"
                  >
                    <span>{p.name}</span>
                    <span className="text-xs text-gray-400">{p.unit_display}</span>
                  </li>
                ))}
              </ul>
            )}
            {dropdownOpen && search && filtered.length === 0 && (
              <div className="absolute right-0 left-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow p-3 z-10 text-sm text-gray-400">
                לא נמצא מוצר בקטלוג
              </div>
            )}
          </div>

          {/* Selected items */}
          {items.length > 0 && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">מוצרים שנבחרו</p>
              <div className="bg-white rounded-xl shadow-sm divide-y divide-gray-100">
                {items.map((item, idx) => (
                  <div key={idx} className="flex items-center gap-3 px-4 py-3">
                    <span className="flex-1 text-sm text-gray-800">{item.name}</span>
                    {(() => {
                      const p = catalog.find(c => c.name === item.name);
                      const unit = p?.unit ?? "";
                      const whole = WHOLE_UNITS.has(unit);
                      return (
                        <div className="flex items-center gap-1">
                          <input
                            type="number"
                            min={whole ? "1" : "0.5"}
                            step={whole ? "1" : "0.5"}
                            value={item.quantity}
                            onChange={(e) => updateQty(idx, whole ? String(Math.round(Number(e.target.value))) : e.target.value)}
                            className="w-20 border border-gray-200 rounded-lg px-2 py-1 text-sm text-center focus:outline-none focus:ring-2 focus:ring-blue-400"
                          />
                          {p?.unit_display && (
                            <span className="text-xs text-gray-500 whitespace-nowrap">{p.unit_display}</span>
                          )}
                        </div>
                      );
                    })()}
                    <button
                      onClick={() => removeItem(idx)}
                      className="text-gray-400 hover:text-red-500 transition text-lg leading-none"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {suggestError && (
            <div className="bg-red-50 border border-red-300 rounded-xl px-4 py-3 flex items-start gap-2">
              <span className="text-red-500 text-lg leading-tight">⚠️</span>
              <p className="text-red-700 text-sm font-semibold">{suggestError}</p>
            </div>
          )}

          <button
            onClick={handleSuggest}
            disabled={items.length === 0 || loadingSuggest}
            className="w-full bg-blue-600 text-white py-2.5 rounded-xl text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
          >
            {loadingSuggest ? "מחשב מחירים..." : "המשך לבחירת ספקים"}
          </button>
        </div>
      )}

      {/* ─── Step 2: Scenarios ────────────────────────────────────────── */}
      {step === 2 && suggestion && (
        <div className="space-y-5">
          {/* Market comparison */}
          {suggestion.market_comparison.total_savings &&
            Number(suggestion.market_comparison.total_savings) > 0 && (
              <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-sm text-green-800">
                <span className="font-semibold">חסכון לעומת מחיר שוק: </span>
                {formatCurrency(suggestion.market_comparison.total_savings)}
              </div>
            )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {(["cheapest", "fewest_suppliers"] as const)
              .map((key) => ({
                key,
                sc: suggestion[key],
                issues: suggestion.minimum_issues[key],
              }))
              .sort((a, b) => Number(a.sc.total_price) - Number(b.sc.total_price))
              .map(({ key, sc, issues }, idx) => {
              const isCheapest = idx === 0;
              const label = isCheapest ? "הכי זול" : (key === "fewest_suppliers" ? "ספק אחד" : "אפשרות נוספת");
              return (
                <div
                  key={key}
                  className={`border-2 rounded-xl p-4 flex flex-col gap-3 transition ${
                    isCheapest
                      ? "border-blue-500 hover:border-blue-600"
                      : "border-gray-200 hover:border-gray-400"
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className={`text-xs font-semibold uppercase tracking-wide ${isCheapest ? "text-blue-600" : "text-gray-500"}`}>
                          {label}
                        </p>
                        {isCheapest && (
                          <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">מומלץ</span>
                        )}
                      </div>
                      <p className="text-2xl font-bold text-gray-900 mt-0.5">
                        {formatCurrency(sc.total_price)}
                      </p>
                    </div>
                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full whitespace-nowrap">
                      {sc.supplier_count} ספקים
                    </span>
                  </div>

                  <ul className="text-xs text-gray-600 space-y-1 flex-1">
                    {sc.products.map((p, i) => {
                      const unitDisplay = catalog.find(c => c.id === p.product_id)?.unit_display ?? p.unit;
                      return (
                        <li key={i} className="flex justify-between gap-2">
                          <span className="truncate">{p.product_name} × {formatQty(p.quantity)} {unitDisplay}</span>
                          <span className="shrink-0 text-gray-400">{p.supplier_name}</span>
                        </li>
                      );
                    })}
                  </ul>

                  {issues.length > 0 && (
                    <div className="bg-orange-50 rounded-lg p-2 text-xs text-orange-700 space-y-0.5">
                      {issues.map((iss) => (
                        <p key={iss.supplier_id}>
                          {iss.supplier_name}: חסר {formatCurrency(iss.missing_amount)} למינימום
                        </p>
                      ))}
                    </div>
                  )}

                  <button
                    onClick={() => handlePlace(key)}
                    disabled={placing || issues.length > 0}
                    className={`w-full py-2 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition ${
                      isCheapest
                        ? "bg-blue-600 text-white hover:bg-blue-700"
                        : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                    }`}
                  >
                    {placing ? "מבצע הזמנה..." : issues.length > 0 ? "⛔ מינימום לא עומד" : `הזמן — ${label}`}
                  </button>
                </div>
              );
            })}
          </div>

          {placeError && (
            <div className="bg-red-50 border border-red-300 rounded-xl px-4 py-3 flex items-start gap-2">
              <span className="text-red-500 text-lg leading-tight">⚠️</span>
              <p className="text-red-700 text-sm font-semibold">{placeError}</p>
            </div>
          )}

          <button
            onClick={() => { setStep(1); setSuggestion(null); }}
            className="text-sm text-gray-500 hover:text-gray-800 transition"
          >
            &larr; חזור לבחירת מוצרים
          </button>
        </div>
      )}

      {/* ─── Step 3: Confirmation ─────────────────────────────────────── */}
      {step === 3 && placed && (
        <div className="space-y-5">
          <div className="bg-green-50 border border-green-200 rounded-xl p-5 text-center">
            <p className="text-lg font-bold text-green-800">הזמנה בוצעה בהצלחה!</p>
            <p className="text-sm text-green-700 mt-1">
              הזמנה #{placed.order_id} — {formatCurrency(placed.total_price)}
            </p>
          </div>

          {placed.whatsapp_links.length > 0 && (
            <div>
              <p className="text-sm font-semibold text-gray-700 mb-3">שלח הזמנה לספקים:</p>
              <div className="space-y-2">
                {placed.whatsapp_links.map((wl) => (
                  <a
                    key={wl.supplier_id}
                    href={wl.whatsapp_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between bg-green-600 text-white px-4 py-3 rounded-xl hover:bg-green-700 transition"
                  >
                    <div>
                      <p className="font-medium">{wl.supplier_name}</p>
                      <p className="text-xs text-green-200">{wl.phone}</p>
                    </div>
                    <span className="text-sm font-medium bg-white/20 px-3 py-1 rounded-lg">
                      פתח WhatsApp
                    </span>
                  </a>
                ))}
              </div>
            </div>
          )}

          <div className="flex gap-3">
            <button
              onClick={() => { localStorage.removeItem("new-order-items"); setStep(1); setItems([]); setSuggestion(null); setPlaced(null); }}
              className="flex-1 border border-gray-300 rounded-xl py-2.5 text-sm text-gray-600 hover:bg-gray-50 transition"
            >
              הזמנה חדשה
            </button>
            <button
              onClick={() => router.push("/dashboard")}
              className="flex-1 bg-blue-600 text-white rounded-xl py-2.5 text-sm font-medium hover:bg-blue-700 transition"
            >
              לוח בקרה
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
