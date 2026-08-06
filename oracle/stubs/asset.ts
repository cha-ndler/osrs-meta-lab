/**
 * Stub for image and style imports.
 *
 * The calculator's enum modules import prayer and potion icons for the web UI
 * (`@/enums/Prayer.ts` pulls in PNGs at module load). Node cannot parse those,
 * and the calculation never reads them - it only needs the enum values. Their
 * jest config solves this with a moduleNameMapper file mock; this is the same
 * idea expressed as a tsconfig path alias.
 */
export default '';
