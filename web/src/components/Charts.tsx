"use client";

import { RISK_THRESHOLDS, hourLabel, riskBand } from "@/lib/format";

const W = 960;
const H = 210;
const BASE = 176;
const LEFT = 16;
const RIGHT = 16;

function x(i: number): number {
  return LEFT + (i * (W - LEFT - RIGHT)) / 23;
}

/** Smooth closed area through hourly values (monotone-ish midpoint curve). */
function terrainPath(values: readonly number[], max: number): { area: string; edge: string } {
  const pts = values.map((v, i) => [x(i), BASE - (Math.max(0, v) / max) * (BASE - 24)] as const);
  if (pts.length === 0) return { area: "", edge: "" };
  let edge = `M ${pts[0]![0].toFixed(1)} ${pts[0]![1].toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) {
    const [px, py] = pts[i - 1]!;
    const [cx, cy] = pts[i]!;
    const mx = (px + cx) / 2;
    edge += ` C ${mx.toFixed(1)} ${py.toFixed(1)}, ${mx.toFixed(1)} ${cy.toFixed(1)}, ${cx.toFixed(1)} ${cy.toFixed(1)}`;
  }
  const last = pts[pts.length - 1]!;
  const area = `${edge} L ${last[0].toFixed(1)} ${BASE} L ${pts[0]![0].toFixed(1)} ${BASE} Z`;
  return { area, edge };
}

export function AttentionHorizon({ hourly, triggerHours, nowHour, score }: {
  hourly: readonly number[]; triggerHours: readonly number[]; nowHour: number | null; score: number;
}) {
  const values = Array.from({ length: 24 }, (_, i) => hourly[i] ?? 0);
  const max = Math.max(10, ...values);
  const { area, edge } = terrainPath(values, max);
  const peak = values.indexOf(Math.max(...values));
  const band = (W - LEFT - RIGHT) / 23;
  const described = `Social-media minutes by hour today. Peak ${Math.round(values[peak] ?? 0)} minutes around ${hourLabel(peak)}.`;
  return (
    <div className="horizon">
      <div>
        <p className="readout-value" aria-label={`Attention score ${score} out of 100`}>
          {score}<span className="readout-scale">/100</span>
        </p>
        <p className="soft">Attention score today</p>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={described}>
        <title>Attention horizon</title>
        {triggerHours.map((h) => (
          <rect key={`t${h}`} className="horizon-trigger" x={Math.max(0, x(h) - band / 2)} y={12} width={band} height={BASE - 12} />
        ))}
        <line className="horizon-axis" x1={LEFT} x2={W - RIGHT} y1={BASE} y2={BASE} />
        <g className="horizon-draw">
          <path className="horizon-terrain" d={area} opacity={0.55} />
          <path className="horizon-edge" d={edge} />
        </g>
        {nowHour !== null ? <line className="horizon-now" x1={x(nowHour)} x2={x(nowHour)} y1={10} y2={BASE} /> : null}
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => (
          <text key={h} className="horizon-label" x={x(h)} y={BASE + 22} textAnchor="middle">{hourLabel(h)}</text>
        ))}
        {triggerHours.length > 0 ? (
          <text className="horizon-label" x={x(triggerHours[0]!) + 6} y={26}>your usual high-usage hours</text>
        ) : null}
      </svg>
    </div>
  );
}

const BAND_COLOR = { low: "var(--growth)", mild: "var(--horizon)", elevated: "var(--signal)", high: "var(--alarm)" } as const;

export function Meter({ label, value, caption }: { label: string; value: number | null; caption?: string }) {
  const band = riskBand(value);
  return (
    <div>
      <div className="spread">
        <span>{label}</span>
        <span className="num" style={{ fontWeight: 600 }}>{value === null ? "No data yet" : `${value} · ${band}`}</span>
      </div>
      <div className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}>
        {value !== null ? <div className="meter-fill" style={{ width: `${Math.min(100, value)}%`, background: BAND_COLOR[band ?? "low"] }} /> : null}
        {[RISK_THRESHOLDS.mild, RISK_THRESHOLDS.elevated, RISK_THRESHOLDS.high].map((t) => (
          <span key={t} className="meter-tick" style={{ left: `${t}%` }} />
        ))}
      </div>
      {caption ? <p className="small soft" style={{ marginTop: "0.3rem" }}>{caption}</p> : null}
    </div>
  );
}

export function Trend({ points, labels, label, format }: {
  points: readonly number[]; labels: readonly string[]; label: string; format: (v: number) => string;
}) {
  const w = 480;
  const h = 120;
  if (points.length < 2) return <p className="soft small">Not enough history for a trend yet.</p>;
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const span = max - min || 1;
  const coords = points.map((p, i) => [(i * (w - 8)) / (points.length - 1) + 4, h - 16 - ((p - min) / span) * (h - 32)] as const);
  const line = coords.map(([cx, cy], i) => `${i === 0 ? "M" : "L"} ${cx.toFixed(1)} ${cy.toFixed(1)}`).join(" ");
  const last = points[points.length - 1]!;
  return (
    <figure style={{ margin: 0 }}>
      <div className="spread"><figcaption>{label}</figcaption><span className="num" style={{ fontWeight: 600 }}>{format(last)}</span></div>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`${label}: from ${format(points[0]!)} to ${format(last)}`}>
        <path d={`${line} L ${coords[coords.length - 1]![0]} ${h - 16} L ${coords[0]![0]} ${h - 16} Z`} fill="var(--horizon-soft)" />
        <path d={line} fill="none" stroke="var(--horizon)" strokeWidth={2} />
        <text x={4} y={h - 2} className="horizon-label">{labels[0]}</text>
        <text x={w - 4} y={h - 2} className="horizon-label" textAnchor="end">{labels[labels.length - 1]}</text>
      </svg>
    </figure>
  );
}

export function BarList({ items }: { items: readonly { label: string; value: number; display: string }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div>
      {items.map((item) => (
        <div className="bar-row" key={item.label}>
          <span>{item.label}</span>
          <div className="bar" style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }} aria-hidden="true" />
          <span className="num small">{item.display}</span>
        </div>
      ))}
    </div>
  );
}
