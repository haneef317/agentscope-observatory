/**
 * Zero-dependency chart components.
 *
 * Replaces `recharts` to avoid its heavy dependency chain (react-smooth,
 * d3-* internals) which caused silent module-graph failures in the
 * production bundle. These components render pure SVG: a smooth area chart
 * and a simple bar chart, both with hover tooltips.
 *
 * Data shape: { bucket: string; value: number }[] — the same shape the
 * backend stats API returns.
 */

import { useMemo, useRef, useState } from "react";

export type SeriesPoint = { bucket: string; value: number };

const MARGIN = { top: 14, right: 12, bottom: 34, left: 44 };

function linearScale(
  values: number[],
  outMin: number,
  outMax: number,
): { scale: (v: number) => number; ticks: number[]; fmt: (v: number) => string } {
  const max = Math.max(...values, 0);
  const nice = max === 0 ? 1 : Math.ceil(max * 1.15);
  const scale = (v: number) => outMax - ((v / nice) * (outMax - outMin));
  const tickCount = 5;
  const step = nice / tickCount;
  const ticks = Array.from({ length: tickCount + 1 }, (_, i) => i * step);
  const fmt = (v: number) =>
    v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Number.isInteger(v) ? String(v) : v.toFixed(2);
  return { scale, ticks, fmt };
}

function useHoverTip(
  xAt: (i: number) => number,
  yAt: (i: number) => number,
) {
  const [tip, setTip] = useState<{ i: number; x: number; y: number } | null>(null);
  const raf = useRef<number | null>(null);
  const update = (i: number) => {
    if (raf.current) cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(() =>
      setTip({ i, x: xAt(i), y: yAt(i) }),
    );
  };
  const clear = () => setTip(null);
  return { tip, update, clear };
}

export function AreaChart({
  data,
  width = 560,
  height = 190,
  stroke = "#5b8def",
  fill = "#2c4070",
}: {
  data: SeriesPoint[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
}) {
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top - MARGIN.bottom;
  const n = Math.max(data.length, 1);
  const xAt = (i: number) => MARGIN.left + (i / (n - 1 || 1)) * innerW;
  const yAt = (i: number) => yScale.scale(data[i]?.value ?? 0);
  const { tip, update, clear } = useHoverTip(xAt, yAt);
  const yScale = useMemo(
    () => linearScale(data.map((d) => d.value), MARGIN.top, MARGIN.top + innerH),
    [data, innerH],
  );

  const pathPoints = data.map((_, i) => [xAt(i), yAt(i)] as const);
  const line = pathPoints.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${xAt(n - 1).toFixed(1)},${(MARGIN.top + innerH).toFixed(1)} L${xAt(0).toFixed(1)},${(MARGIN.top + innerH).toFixed(1)} Z`;

  return (
    <svg width={width} height={height} style={{ maxWidth: "100%", height: "auto" }}>
      {yScale.ticks.map((t) => (
        <g key={t}>
          <line x1={MARGIN.left} x2={width - MARGIN.right} y1={yScale.scale(t)} y2={yScale.scale(t)} stroke="#1f2a3e" />
          <text x={MARGIN.left - 6} y={yScale.scale(t) + 3} textAnchor="end" fontSize={10} fill="#8b98ad">
            {yScale.fmt(t)}
          </text>
        </g>
      ))}
      {data.map((d, i) => (
        <text key={i} x={xAt(i)} y={height - 8} textAnchor="middle" fontSize={9} fill="#8b98ad">
          {d.bucket}
        </text>
      ))}
      <path d={area} fill={fill} />
      <path d={line} fill="none" stroke={stroke} strokeWidth={2} />
      {tip && (
        <g>
          <line x1={xAt(tip.i)} x2={xAt(tip.i)} y1={MARGIN.top} y2={MARGIN.top + innerH} stroke="#5b8def" strokeDasharray="3 3" />
          <circle cx={xAt(tip.i)} cy={yAt(tip.i)} r={4} fill={stroke} />
        </g>
      )}
      {data.map((_, i) => (
        <rect
          key={i}
          x={xAt(i) - innerW / n / 2}
          y={MARGIN.top}
          width={innerW / n}
          height={innerH}
          fill="transparent"
          style={{ cursor: "crosshair" }}
          onMouseMove={() => update(i)}
          onMouseLeave={clear}
        />
      ))}
      {tip && (
        <g>
          <rect
            x={Math.min(xAt(tip.i) + 8, width - 128)}
            y={Math.max(yAt(tip.i) - 36, 4)}
            width={120}
            height={28}
            rx={6}
            fill="#111826"
            stroke="#1f2a3e"
          />
          <text
            x={Math.min(xAt(tip.i) + 8, width - 128) + 8}
            y={Math.max(yAt(tip.i) - 36, 4) + 18}
            fontSize={11}
            fill="#cdd7e5"
          >
            {data[tip.i].bucket} · {data[tip.i].value}
          </text>
        </g>
      )}
    </svg>
  );
}

export function BarChart({
  data,
  width = 560,
  height = 190,
  fill = "#3ddc97",
}: {
  data: SeriesPoint[];
  width?: number;
  height?: number;
  fill?: string;
}) {
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top - MARGIN.bottom;
  const n = Math.max(data.length, 1);
  const yScale = useMemo(
    () => linearScale(data.map((d) => d.value), MARGIN.top, MARGIN.top + innerH),
    [data, innerH],
  );
  const bw = Math.max(6, innerW / n - 4);
  const xAt = (i: number) => MARGIN.left + (i + 0.5) * (innerW / n);
  const yAt = (i: number) => yScale.scale(data[i]?.value ?? 0);
  const { tip, update, clear } = useHoverTip(xAt, yAt);

  return (
    <svg width={width} height={height} style={{ maxWidth: "100%", height: "auto" }}>
      {yScale.ticks.map((t) => (
        <g key={t}>
          <line x1={MARGIN.left} x2={width - MARGIN.right} y1={yScale.scale(t)} y2={yScale.scale(t)} stroke="#1f2a3e" />
          <text x={MARGIN.left - 6} y={yScale.scale(t) + 3} textAnchor="end" fontSize={10} fill="#8b98ad">
            {yScale.fmt(t)}
          </text>
        </g>
      ))}
      {data.map((d, i) => (
        <g key={i}>
          <rect
            x={xAt(i) - bw / 2}
            y={yAt(i)}
            width={bw}
            height={Math.max(1, MARGIN.top + innerH - yAt(i))}
            rx={3}
            fill={fill}
            opacity={tip?.i === i ? 1 : 0.85}
          />
          <text x={xAt(i)} y={height - 8} textAnchor="middle" fontSize={9} fill="#8b98ad">
            {d.bucket}
          </text>
          <rect
            x={xAt(i) - innerW / n / 2}
            y={MARGIN.top}
            width={innerW / n}
            height={innerH}
            fill="transparent"
            style={{ cursor: "crosshair" }}
            onMouseMove={() => update(i)}
            onMouseLeave={clear}
          />
        </g>
      ))}
      {tip && (
        <g>
          <rect
            x={Math.min(xAt(tip.i) + 8, width - 128)}
            y={Math.max(yAt(tip.i) - 36, 4)}
            width={120}
            height={28}
            rx={6}
            fill="#111826"
            stroke="#1f2a3e"
          />
          <text
            x={Math.min(xAt(tip.i) + 8, width - 128) + 8}
            y={Math.max(yAt(tip.i) - 36, 4) + 18}
            fontSize={11}
            fill="#cdd7e5"
          >
            {data[tip.i].bucket} · {data[tip.i].value}
          </text>
        </g>
      )}
    </svg>
  );
}
