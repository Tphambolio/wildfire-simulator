/**
 * Inline SVG of an asset category's map symbol (card rows, filter chips). Decorative: the row
 * or chip text names the category. Drawn on a 2x box so the letter is a graphic, not small text.
 */

import { ASSET_CATEGORIES, type AssetCategory } from "../utils/assets";
import { SYMBOL_PX, shapePath, symbolColours } from "../utils/assetSymbols";

const BOX = SYMBOL_PX * 2;

export default function AssetSymbol({ category, reached = false, size = 20 }: { category: AssetCategory; reached?: boolean; size?: number }) {
  const style = ASSET_CATEGORIES[category];
  const col = symbolColours(reached);
  return (
    <svg className="asset-symbol" width={size} height={size} viewBox={`0 0 ${BOX} ${BOX}`} aria-hidden="true" focusable="false">
      <path d={shapePath(style.shape, BOX)} fill={col.fill} stroke={col.stroke} strokeWidth={reached ? 4 : 3} />
      <text
        x={BOX / 2}
        y={BOX / 2 + (style.shape === "triangle" ? 6 : 1)}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={style.letter.length > 1 ? 18 : 22}
        fontWeight={700}
        fill={col.text}
      >
        {style.letter}
      </text>
    </svg>
  );
}
