/**
 * Starting-codes source line under the weather fields.
 *
 * The API's "pyra" tier (`source_tier: "pyra"`) returns a complete label in `source`
 * ("Pyra · <station> · as of noon LST … (chain …)"); it is shown as is, so the user can find the
 * same numbers on Pyra's station page. Other tiers keep the station · distance · date line.
 */
export function isPyraSource(source: string | null | undefined): source is string {
  return typeof source === "string" && source.startsWith("Pyra · ");
}
