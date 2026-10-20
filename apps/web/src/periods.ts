// The month a document belongs to, for grouping the list.

/** "115/08/01", "2026/08/01" or "2026-08-01" as a date (ROC years too). */
function parseDate(text: string): Date | null {
  const match = text.match(/(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})/);
  if (!match) return null;
  let year = Number(match[1]);
  if (year < 1000) year += 1911;
  const date = new Date(Date.UTC(year, Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) || date.getUTCMonth() !== Number(match[2]) - 1 ? null : date;
}

const monthOf = (date: Date) => `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;

/** The month with most of a period's days ("115/07/15~115/08/14" → 2026-07); null when it cannot be read. */
export function periodMonth(period: string | null | undefined): string | null {
  if (!period) return null;
  const dates = [...period.matchAll(/\d{2,4}\s*[/.\-年]\s*\d{1,2}\s*[/.\-月]\s*\d{1,2}/g)]
    .map((match) => parseDate(match[0]))
    .filter((date): date is Date => date !== null);
  if (!dates.length) return null;
  const [start, end] = [dates[0], dates[dates.length - 1] < dates[0] ? dates[0] : dates[dates.length - 1]];
  const days = new Map<string, number>();
  for (let day = start.getTime(); day <= end.getTime(); day += 86_400_000) {
    const month = monthOf(new Date(day));
    days.set(month, (days.get(month) ?? 0) + 1);
  }
  // Most days; on a tie, the earlier month.
  return [...days.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
}
