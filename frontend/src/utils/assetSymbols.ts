/**
 * Asset symbols: a shape per category with its letter, so categories differ without colour
 * (design spec §7, 1.4.1). Not reached: white with ink; reached by the modelled fire: ink with
 * white (and a text label). The same paths draw the map icons (canvas, no map glyphs needed)
 * and the card's inline SVG.
 */

import { ASSET_CATEGORIES, type AssetCategory, type SymbolShape } from "./assets";

/** Icon box in CSS pixels (the map hit target is at least this; WCAG 2.5.8 minimum 24 px). */
export const SYMBOL_PX = 24;

const INK = "#111827";
const PAPER = "#ffffff";

/** SVG path of the shape in a size x size box (1 px inset for the stroke). */
export function shapePath(shape: SymbolShape, size: number): string {
  const c = size / 2;
  const r = size / 2 - 1.5;
  const poly = (n: number, rot: number, rr = r) =>
    Array.from({ length: n }, (_, i) => {
      const a = rot + (i * 2 * Math.PI) / n;
      return `${(c + rr * Math.cos(a)).toFixed(2)},${(c + rr * Math.sin(a)).toFixed(2)}`;
    });
  switch (shape) {
    case "circle":
      return `M ${c - r},${c} a ${r},${r} 0 1,0 ${2 * r},0 a ${r},${r} 0 1,0 ${-2 * r},0 Z`;
    case "square": {
      const s = r * 0.9;
      return `M ${c - s},${c - s} H ${c + s} V ${c + s} H ${c - s} Z`;
    }
    case "diamond":
      return `M ${poly(4, -Math.PI / 2).join(" L ")} Z`;
    case "hexagon":
      return `M ${poly(6, 0).join(" L ")} Z`;
    case "triangle": {
      const pts = poly(3, -Math.PI / 2, r * 1.08).map((p) => p.split(",").map(Number));
      // Shift down so the letter sits in the wider part
      return `M ${pts.map(([x, y]) => `${x.toFixed(2)},${(y + r * 0.18).toFixed(2)}`).join(" L ")} Z`;
    }
    case "star": {
      const pts: string[] = [];
      for (let i = 0; i < 10; i++) {
        const rr = i % 2 === 0 ? r : r * 0.55;
        const a = -Math.PI / 2 + (i * Math.PI) / 5;
        pts.push(`${(c + rr * Math.cos(a)).toFixed(2)},${(c + rr * Math.sin(a)).toFixed(2)}`);
      }
      return `M ${pts.join(" L ")} Z`;
    }
  }
}

export function symbolColours(reached: boolean): { fill: string; stroke: string; text: string } {
  return reached ? { fill: INK, stroke: PAPER, text: PAPER } : { fill: PAPER, stroke: INK, text: INK };
}

/**
 * The icon as RGBA pixels for map.addImage (pixelRatio for sharp HiDPI icons). Returns null
 * where there is no canvas (tests).
 */
export function symbolImage(category: AssetCategory, reached: boolean, pixelRatio = 2): ImageData | null {
  if (typeof document === "undefined") return null;
  const px = SYMBOL_PX * pixelRatio;
  const canvas = document.createElement("canvas");
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext("2d");
  if (!ctx || typeof Path2D === "undefined") return null;
  ctx.scale(pixelRatio, pixelRatio);
  const style = ASSET_CATEGORIES[category];
  const col = symbolColours(reached);
  const path = new Path2D(shapePath(style.shape, SYMBOL_PX));
  ctx.fillStyle = col.fill;
  ctx.fill(path);
  ctx.lineWidth = reached ? 2 : 1.5;
  ctx.strokeStyle = col.stroke;
  ctx.stroke(path);
  ctx.fillStyle = col.text;
  ctx.font = `700 ${style.letter.length > 1 ? 9 : 11}px Inter, "Segoe UI", Roboto, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const dy = style.shape === "triangle" ? 3 : 0.5;
  ctx.fillText(style.letter, SYMBOL_PX / 2, SYMBOL_PX / 2 + dy);
  return ctx.getImageData(0, 0, px, px);
}
