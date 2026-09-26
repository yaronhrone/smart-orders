// Shared order-status display — one order = one supplier, so every page
// that lists orders (customer dashboard, order detail, admin) shows the
// same labels and colors.

export const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending:   { label: "ממתין",       color: "bg-yellow-100 text-yellow-800" },
  sent:      { label: "נשלח לספק",   color: "bg-purple-100 text-purple-800" },
  approved:  { label: "אושר",        color: "bg-blue-100 text-blue-800" },
  shipped:   { label: "יצא למשלוח",  color: "bg-indigo-100 text-indigo-800" },
  delivered: { label: "נמסר",        color: "bg-green-100 text-green-800" },
  cancelled: { label: "בוטל",        color: "bg-red-100 text-red-800" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS_LABELS[status] ?? { label: status, color: "bg-gray-100 text-gray-700" };
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${s.color}`}>
      {s.label}
    </span>
  );
}

/** "2/3 נמסרו" — how many of a checkout's live (non-cancelled) orders arrived. */
export function deliveredSummary(orders: { status: string }[]): string | null {
  const live = orders.filter((o) => o.status !== "cancelled");
  if (live.length < 2) return null;
  const delivered = live.filter((o) => o.status === "delivered").length;
  return `${delivered}/${live.length} נמסרו`;
}

export function formatCurrency(n: string | number) {
  return `₪${Number(n).toLocaleString("he-IL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString("he-IL", {
    day: "numeric",
    month: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
