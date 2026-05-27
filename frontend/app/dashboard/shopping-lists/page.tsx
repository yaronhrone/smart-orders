"use client";

import { useEffect, useState } from "react";
import {
  fetchShoppingLists,
  createShoppingList,
  updateShoppingList,
  deleteShoppingList,
  suggestFromShoppingList,
  placeOrder,
  fetchMe,
  ShoppingList,
  ShoppingListItem,
  SuggestOrderResponse,
  PlaceOrderResponse,
} from "../../lib/api";

function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

type ModalMode = "create" | "edit";

const EMPTY_LIST = { name: "", is_primary: false, products: [] as ShoppingListItem[] };

export default function ShoppingListsPage() {
  const [lists, setLists] = useState<ShoppingList[] | null>(null);
  const [error, setError] = useState("");
  const [region, setRegion] = useState("center");

  // Modal
  const [modal, setModal] = useState<{ mode: ModalMode; list: typeof EMPTY_LIST; editId?: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [modalError, setModalError] = useState("");

  // Suggest modal
  const [suggesting, setSuggesting] = useState<number | null>(null);
  const [suggestion, setSuggestion] = useState<SuggestOrderResponse | null>(null);
  const [placing, setPlacing] = useState(false);
  const [placed, setPlaced] = useState<PlaceOrderResponse | null>(null);
  const [suggestError, setSuggestError] = useState("");

  useEffect(() => {
    load();
    fetchMe()
      .then((me) => {
        if (me.profile?.region) setRegion(me.profile.region);
      })
      .catch(() => {});
  }, []);

  async function load() {
    try {
      const data = await fetchShoppingLists();
      setLists(data);
    } catch {
      setError("שגיאה בטעינת הרשימות");
    }
  }

  // ─── modal helpers ───────────────────────────────────────────────────────

  function openCreate() {
    setModal({ mode: "create", list: { ...EMPTY_LIST, products: [] } });
    setModalError("");
  }

  function openEdit(list: ShoppingList) {
    setModal({
      mode: "edit",
      editId: list.id,
      list: {
        name: list.name,
        is_primary: list.is_primary,
        products: list.products.map((p) => ({ product_name: p.product_name, default_quantity: p.default_quantity })),
      },
    });
    setModalError("");
  }

  function addProductRow() {
    setModal((prev) =>
      prev ? { ...prev, list: { ...prev.list, products: [...prev.list.products, { product_name: "", default_quantity: "1" }] } } : prev
    );
  }

  function removeProductRow(idx: number) {
    setModal((prev) =>
      prev ? { ...prev, list: { ...prev.list, products: prev.list.products.filter((_, i) => i !== idx) } } : prev
    );
  }

  function updateProductRow(idx: number, field: keyof ShoppingListItem, value: string) {
    setModal((prev) => {
      if (!prev) return prev;
      const products = prev.list.products.map((p, i) =>
        i === idx ? { ...p, [field]: value } : p
      );
      return { ...prev, list: { ...prev.list, products } };
    });
  }

  async function handleSave() {
    if (!modal) return;
    if (!modal.list.name.trim()) { setModalError("שם הרשימה נדרש"); return; }
    if (modal.list.products.length === 0) { setModalError("יש להוסיף לפחות מוצר אחד"); return; }
    setSaving(true);
    setModalError("");
    try {
      const payload = {
        name: modal.list.name.trim(),
        is_primary: modal.list.is_primary,
        products: modal.list.products.filter((p) => p.product_name.trim()),
      };
      if (modal.mode === "create") {
        await createShoppingList(payload);
      } else if (modal.editId !== undefined) {
        await updateShoppingList(modal.editId, payload);
      }
      setModal(null);
      await load();
    } catch (e: unknown) {
      setModalError(e instanceof Error ? e.message : "שגיאה בשמירה");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number, name: string) {
    if (!confirm(`למחוק את "${name}"?`)) return;
    try {
      await deleteShoppingList(id);
      setLists((prev) => prev?.filter((l) => l.id !== id) ?? null);
    } catch {
      alert("שגיאה במחיקת הרשימה");
    }
  }

  // ─── suggest from list ───────────────────────────────────────────────────

  async function handleSuggest(id: number) {
    setSuggesting(id);
    setSuggestion(null);
    setPlaced(null);
    setSuggestError("");
    try {
      const result = await suggestFromShoppingList(id, region);
      setSuggestion(result);
    } catch (e: unknown) {
      setSuggestError(e instanceof Error ? e.message : "שגיאה בהבאת הצעות מחיר");
    }
  }

  async function handlePlace(scenario: "cheapest" | "fewest_suppliers") {
    if (!suggesting || !suggestion) return;
    setPlacing(true);
    setSuggestError("");
    const list = lists?.find((l) => l.id === suggesting);
    if (!list) return;
    try {
      const result = await placeOrder(
        list.products.map((p) => ({ product_name: p.product_name, quantity: p.default_quantity })),
        scenario,
        region
      );
      setPlaced(result);
    } catch (e: unknown) {
      setSuggestError(e instanceof Error ? e.message : "שגיאה בביצוע ההזמנה");
    } finally {
      setPlacing(false);
    }
  }

  // ─── render ──────────────────────────────────────────────────────────────

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }

  if (!lists) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">רשימות קניות</h1>
        <button
          onClick={openCreate}
          className="bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700 transition"
        >
          + רשימה חדשה
        </button>
      </div>

      {lists.length === 0 ? (
        <p className="text-sm text-gray-500">אין רשימות עדיין. לחץ על &ldquo;רשימה חדשה&rdquo;.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {lists.map((list) => (
            <div key={list.id} className="bg-white rounded-xl shadow-sm p-4 flex flex-col gap-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold text-gray-800">{list.name}</h2>
                  {list.is_primary && (
                    <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
                      ראשית
                    </span>
                  )}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => openEdit(list)}
                    className="text-xs text-gray-400 hover:text-blue-600 px-2 py-1 rounded transition"
                  >
                    עריכה
                  </button>
                  <button
                    onClick={() => handleDelete(list.id, list.name)}
                    className="text-xs text-gray-400 hover:text-red-600 px-2 py-1 rounded transition"
                  >
                    מחק
                  </button>
                </div>
              </div>

              <ul className="text-sm text-gray-600 space-y-0.5">
                {list.products.slice(0, 4).map((p, i) => (
                  <li key={i} className="flex justify-between">
                    <span>{p.product_name}</span>
                    <span className="text-gray-400">{p.default_quantity}</span>
                  </li>
                ))}
                {list.products.length > 4 && (
                  <li className="text-gray-400 text-xs">+{list.products.length - 4} עוד...</li>
                )}
              </ul>

              <button
                onClick={() => handleSuggest(list.id)}
                className="mt-auto w-full bg-green-600 text-white text-sm font-medium py-2 rounded-lg hover:bg-green-700 transition"
              >
                הזמן עכשיו
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ─── Edit / Create modal ─────────────────────────────────────────── */}
      {modal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">
                {modal.mode === "create" ? "רשימה חדשה" : "עריכת רשימה"}
              </h2>
              <button onClick={() => setModal(null)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">שם הרשימה</label>
                <input
                  type="text"
                  value={modal.list.name}
                  onChange={(e) => setModal((prev) => prev ? { ...prev, list: { ...prev.list, name: e.target.value } } : prev)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="לדוגמה: הזמנה שבועית"
                />
              </div>

              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={modal.list.is_primary}
                  onChange={(e) => setModal((prev) => prev ? { ...prev, list: { ...prev.list, is_primary: e.target.checked } } : prev)}
                  className="rounded"
                />
                רשימה ראשית
              </label>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs font-medium text-gray-700">מוצרים</label>
                  <button
                    onClick={addProductRow}
                    className="text-xs text-blue-600 hover:text-blue-800 transition"
                  >
                    + הוסף מוצר
                  </button>
                </div>
                <div className="space-y-2">
                  {modal.list.products.map((p, i) => (
                    <div key={i} className="flex gap-2 items-center">
                      <input
                        type="text"
                        value={p.product_name}
                        onChange={(e) => updateProductRow(i, "product_name", e.target.value)}
                        placeholder="שם מוצר"
                        className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                      />
                      <input
                        type="text"
                        value={p.default_quantity}
                        onChange={(e) => updateProductRow(i, "default_quantity", e.target.value)}
                        placeholder="כמות"
                        className="w-20 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                      />
                      <button
                        onClick={() => removeProductRow(i)}
                        className="text-gray-400 hover:text-red-500 transition text-lg leading-none"
                      >
                        &times;
                      </button>
                    </div>
                  ))}
                  {modal.list.products.length === 0 && (
                    <p className="text-xs text-gray-400">לחץ על &ldquo;הוסף מוצר&rdquo; להוספת מוצרים</p>
                  )}
                </div>
              </div>

              {modalError && <p className="text-red-600 text-sm">{modalError}</p>}

              <div className="flex gap-3 pt-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? "שומר..." : "שמור"}
                </button>
                <button
                  onClick={() => setModal(null)}
                  className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-600 hover:bg-gray-50"
                >
                  ביטול
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─── Suggest modal ───────────────────────────────────────────────── */}
      {suggesting !== null && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6" dir="rtl">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-lg font-bold text-gray-800">
                {placed ? "הזמנה בוצעה" : "בחר הצעת מחיר"}
              </h2>
              <button
                onClick={() => { setSuggesting(null); setSuggestion(null); setPlaced(null); setSuggestError(""); }}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>

            {!suggestion && !suggestError && (
              <p className="text-gray-500 text-center py-8">מחשב הצעות מחיר...</p>
            )}

            {suggestError && <p className="text-red-600 text-sm">{suggestError}</p>}

            {placed && (
              <div className="space-y-4">
                <div className="bg-green-50 rounded-xl p-4 text-green-800 text-sm font-medium">
                  הזמנה #{placed.order_id} נוצרה בהצלחה — {formatCurrency(placed.total_price)}
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
                          className="flex items-center justify-between bg-green-600 text-white px-4 py-2.5 rounded-lg hover:bg-green-700 transition"
                        >
                          <span className="font-medium">{wl.supplier_name}</span>
                          <span className="text-xs opacity-80">פתח WhatsApp</span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {suggestion && !placed && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {(["cheapest", "fewest_suppliers"] as const).map((key) => {
                  const sc = suggestion[key];
                  const label = key === "cheapest" ? "זול ביותר" : "ספקים מינימלי";
                  const issues = suggestion.minimum_issues[key];
                  return (
                    <div key={key} className="border border-gray-200 rounded-xl p-4 flex flex-col gap-3">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-xs text-gray-500">{label}</p>
                          <p className="text-xl font-bold text-gray-900">
                            {formatCurrency(sc.total_price)}
                          </p>
                        </div>
                        <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                          {sc.supplier_count} ספקים
                        </span>
                      </div>

                      <ul className="text-xs text-gray-600 space-y-0.5">
                        {sc.products.map((p, i) => (
                          <li key={i} className="flex justify-between">
                            <span>{p.product_name} × {p.quantity}</span>
                            <span>{p.supplier_name}</span>
                          </li>
                        ))}
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
                        disabled={placing}
                        className="mt-auto w-full bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
                      >
                        {placing ? "מבצע..." : `הזמן — ${label}`}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
