"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Series = { key: string; label: string; color: string };
type Row = Record<string, number | string>;

const HEIGHT = 200;
const PAD = { top: 12, right: 12, bottom: 24, left: 32 };

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** A "nice" axis maximum and three or four ticks. */
function ticks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / 3;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
}

function shortDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x - 80, 0), Math.max(width - 160, 0));
  return (
    <div
      className="pointer-events-none absolute top-0 z-10 w-40 rounded-md border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-md"
      style={{ left }}
      role="status"
    >
      {children}
    </div>
  );
}

function TooltipRows({ row, series, label }: { row: Row; series: Series[]; label: string }) {
  return (
    <>
      <div className="mb-1 font-medium">{label}</div>
      {series.map((s) => (
        <div key={s.key} className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <span className="inline-block size-2 rounded-sm" style={{ background: s.color }} aria-hidden />
            {s.label}
          </span>
          <span className="tabular-nums">{Number(row[s.key] ?? 0)}</span>
        </div>
      ))}
    </>
  );
}

/** The data behind a chart, as a table (for screen readers and anyone who prefers it). */
export function DataTable({ rows, series, caption }: { rows: Row[]; series: Series[]; caption: string }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
        Show as a table
      </summary>
      <div className="mt-2 max-h-56 overflow-auto rounded-md border">
        <table className="w-full tabular-nums">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-muted">
            <tr>
              <th className="px-2 py-1 text-left font-medium">Day</th>
              {series.map((s) => (
                <th key={s.key} className="px-2 py-1 text-right font-medium">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.day)} className="border-t">
                <td className="px-2 py-1">{shortDay(String(row.day))}</td>
                {series.map((s) => (
                  <td key={s.key} className="px-2 py-1 text-right">
                    {Number(row[s.key] ?? 0)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Axes({
  width,
  max,
  rows,
  x,
}: {
  width: number;
  max: number;
  rows: Row[];
  x: (i: number) => number;
}) {
  const scale = (v: number) => PAD.top + (HEIGHT - PAD.top - PAD.bottom) * (1 - v / max);
  const every = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(width / 70))));
  return (
    <g aria-hidden>
      {ticks(max).map((t) => (
        <g key={t}>
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={scale(t)}
            y2={scale(t)}
            className="stroke-border"
            strokeWidth={1}
          />
          <text x={PAD.left - 6} y={scale(t)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px]">
            {t}
          </text>
        </g>
      ))}
      {rows.map((row, i) =>
        i % every === 0 ? (
          <text
            key={String(row.day)}
            x={x(i)}
            y={HEIGHT - 6}
            textAnchor="middle"
            className="fill-muted-foreground text-[10px]"
          >
            {shortDay(String(row.day))}
          </text>
        ) : null,
      )}
    </g>
  );
}

/** Lines over days, with a crosshair and a tooltip for the hovered day. */
export function DailyLines({ rows, series, label }: { rows: Row[]; series: Series[]; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...ticks(Math.max(1, ...rows.flatMap((r) => series.map((s) => Number(r[s.key] ?? 0))))));
  const inner = Math.max(1, width - PAD.left - PAD.right);
  const x = (i: number) => PAD.left + (rows.length > 1 ? (inner * i) / (rows.length - 1) : inner / 2);
  const y = (v: number) => PAD.top + (HEIGHT - PAD.top - PAD.bottom) * (1 - v / max);

  return (
    <div className="space-y-2">
      <Legend series={series} />
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {width > 0 ? (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label={label}
            onMouseMove={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              const position = event.clientX - box.left - PAD.left;
              const index = Math.round((position / inner) * (rows.length - 1));
              setHover(Math.min(rows.length - 1, Math.max(0, index)));
            }}
          >
            <Axes width={width} max={max} rows={rows} x={x} />
            {hover !== null ? (
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="stroke-muted-foreground/50" />
            ) : null}
            {series.map((s) => (
              <polyline
                key={s.key}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                points={rows.map((r, i) => `${x(i)},${y(Number(r[s.key] ?? 0))}`).join(" ")}
              />
            ))}
            {hover !== null
              ? series.map((s) => (
                  <circle
                    key={s.key}
                    cx={x(hover)}
                    cy={y(Number(rows[hover][s.key] ?? 0))}
                    r={4}
                    fill={s.color}
                    className="stroke-card"
                    strokeWidth={2}
                  />
                ))
              : null}
          </svg>
        ) : (
          <div style={{ height: HEIGHT }} />
        )}
        {hover !== null && width > 0 ? (
          <Tooltip x={x(hover)} width={width}>
            <TooltipRows row={rows[hover]} series={series} label={shortDay(String(rows[hover].day))} />
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}

/** Stacked bars per day (2px surface gaps between segments), tooltip per bar. */
export function DailyStackedBars({ rows, series, label }: { rows: Row[]; series: Series[]; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const totals = rows.map((r) => series.reduce((sum, s) => sum + Number(r[s.key] ?? 0), 0));
  const max = Math.max(...ticks(Math.max(1, ...totals)));
  const inner = Math.max(1, width - PAD.left - PAD.right);
  const slot = inner / Math.max(1, rows.length);
  const barWidth = Math.max(2, Math.min(24, slot * 0.7));
  const x = (i: number) => PAD.left + slot * i + slot / 2;
  const h = (v: number) => ((HEIGHT - PAD.top - PAD.bottom) * v) / max;
  const base = HEIGHT - PAD.bottom;

  return (
    <div className="space-y-2">
      <Legend series={series} />
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {width > 0 ? (
          <svg width={width} height={HEIGHT} role="img" aria-label={label}>
            <Axes width={width} max={max} rows={rows} x={x} />
            {rows.map((row, i) => {
              let top = base;
              const segments = series
                .map((s) => ({ s, value: Number(row[s.key] ?? 0) }))
                .filter((segment) => segment.value > 0);
              return (
                <g key={String(row.day)} onMouseEnter={() => setHover(i)} opacity={hover === null || hover === i ? 1 : 0.55}>
                  {/* Hit target wider and taller than the bar. */}
                  <rect x={x(i) - slot / 2} y={PAD.top} width={slot} height={base - PAD.top} fill="transparent" />
                  {segments.map(({ s, value }, index) => {
                    const height = Math.max(1, h(value) - (index > 0 ? 2 : 0));
                    top -= h(value);
                    const isTop = index === segments.length - 1;
                    return (
                      <rect
                        key={s.key}
                        x={x(i) - barWidth / 2}
                        y={top}
                        width={barWidth}
                        height={height}
                        rx={isTop ? Math.min(4, barWidth / 2) : 0}
                        fill={s.color}
                      />
                    );
                  })}
                </g>
              );
            })}
          </svg>
        ) : (
          <div style={{ height: HEIGHT }} />
        )}
        {hover !== null && width > 0 ? (
          <Tooltip x={x(hover)} width={width}>
            <TooltipRows row={rows[hover]} series={series} label={shortDay(String(rows[hover].day))} />
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}

/** Horizontal bars for a handful of categories (one series), largest first. */
export function CategoryBars({
  items,
  color = "var(--viz-1)",
}: {
  items: { label: string; value: number; hint?: string }[];
  color?: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.label} className="space-y-1" title={item.hint}>
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span>{item.label}</span>
            <span className="text-muted-foreground tabular-nums">{item.value}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
            <div
              className={cn("h-full rounded-full")}
              style={{ width: `${(item.value / max) * 100}%`, background: color, minWidth: item.value ? 4 : 0 }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
