"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Note, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import { reportHref } from "@/lib/links";

type Point = { document_id: string; doc_date: string | null; value: string; unit: string; ref_range: string; source_page: number };
type Series = { name: string; comparable: boolean; conversion_performed: boolean; reason?: string; note?: string; points: Point[];
  observed_change?: { first: string; last: string; unit: string; direction: string } };

const num = (v: string) => { const n = parseFloat(v.replace(",", ".")); return Number.isFinite(n) ? n : null; };

function Spark({ values }: { values: number[] }) {
  const w = 160, h = 40, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / Math.max(values.length - 1, 1)) * (w - 8) + 4},${h - 4 - ((v - min) / span) * (h - 8)}`);
  return <svg viewBox={`0 0 ${w} ${h}`} className="h-10 w-40" aria-hidden><polyline points={pts.join(" ")} fill="none" stroke="var(--teal)" strokeWidth="2" />{pts.map((p, i) => <circle key={i} cx={p.split(",")[0]} cy={p.split(",")[1]} r="3" fill="var(--teal-dark)" />)}</svg>;
}

/** The same test across your uploaded reports. Shows the printed numbers only: no unit conversion, no judgement about whether a change matters. */
export default function ResultsOverTime() {
  const [series, setSeries] = useState<Series[] | null>(null);
  useEffect(() => { api<Series[]>("/api/documents/compare").then(setSeries).catch(() => setSeries([])); }, []);
  if (!series || series.length === 0) return null;
  return (
    <section aria-label="Results over time" className="space-y-3">
      <h2>Results over time</h2>
      <Note tone="info">These are the numbers printed on your reports, side by side. They are not converted and no meaning is added. Your doctor can tell you whether a difference matters.</Note>
      {series.map((s) => {
        const vals = s.points.map((p) => num(p.value));
        const plot = s.comparable && vals.every((v) => v !== null) ? (vals as number[]) : null;
        return (
          <div key={s.name} className="card space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2"><strong>{s.name}</strong>{plot && <Spark values={plot} />}</div>
            {!s.comparable && <p className="text-sm text-muted">{s.reason}</p>}
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`${s.name} results table`}>
              <table><thead><tr><th>Report date</th><th>Printed value</th><th>Printed range</th><th>Source</th></tr></thead><tbody>
                {s.points.map((p) => (
                  <tr key={p.document_id}><td>{p.doc_date ? fmtDate(Date.parse(p.doc_date) / 1000) : "No date found"}</td><td>{p.value} {p.unit}</td><td>{p.ref_range || "Not printed"}</td>
                    <td><Link className="underline" href={reportHref(p.document_id)}>Page {p.source_page}</Link></td></tr>))}
              </tbody></table></div>
            {s.observed_change && <p className="text-sm">First {s.observed_change.first} {s.observed_change.unit}, latest {s.observed_change.last} {s.observed_change.unit} ({s.observed_change.direction} than the first).</p>}
          </div>);
      })}
    </section>
  );
}
