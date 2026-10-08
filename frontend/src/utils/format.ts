/** "0,0123 $" - small cloud costs need more than two decimals. */
export function formatUsd(value: number): string {
  const digits = value > 0 && value < 0.1 ? 4 : 2;
  return `${value.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits })} $`;
}
