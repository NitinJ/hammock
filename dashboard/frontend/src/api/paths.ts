/**
 * Frontend mirror of `shared/v1/paths.py:iter_token`.
 *
 * Stringifies an iter_path tuple so it can appear in URLs and query keys:
 *
 *   []        → "top"
 *   [0]       → "i0"
 *   [0, 1]    → "i0_1"
 *   [2, 0, 4] → "i2_0_4"
 *
 * Backend is the source of truth (`v1_paths.iter_token`); this helper
 * exists so the frontend can compose chat / node-detail URLs without a
 * round-trip. Kept tiny and pure so it can be tested in isolation.
 */
export function iterToken(iter: readonly number[]): string {
  for (const i of iter) {
    if (!Number.isInteger(i) || i < 0) {
      throw new Error(`iter component must be a non-negative integer, got ${i}`);
    }
  }
  if (iter.length === 0) return "top";
  return "i" + iter.join("_");
}
