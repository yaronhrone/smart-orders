const HEBREW_MONTHS = [
  "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
  "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
];

/** "2026-10" -> "אוקטובר 2026" */
export function monthLabel(yyyyMm: string): string {
  const [year, month] = yyyyMm.split("-").map(Number);
  const name = HEBREW_MONTHS[month - 1] ?? yyyyMm;
  return `${name} ${year}`;
}
