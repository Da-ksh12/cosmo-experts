"use client";

import { Check, X } from "lucide-react";
import { Details, Note, Pill, StatusBadge } from "@/components/ui";

type PerClass = Record<string, { n: number; sensitivity: number | null; specificity: number; precision: number | null; auroc: number | null; false_negatives: number; false_positives: number }>;
export type Model = {
  model_id: string; task: string; architecture: string; status: string; dataset_version?: number; classes?: string[]; epochs?: number; finetune?: string; device?: string;
  pretrained_weights: string; pretrained_sha256?: string | null; pretrained_provenance?: string; code_revision?: string; artifact_sha256?: string; training_seconds?: number;
  split_sizes?: Record<string, number>; patients_per_split?: Record<string, number>; near_duplicates_vs_train?: { val: number; test: number };
  dataset_license?: { spdx?: string; official_license_name?: string; source?: string; attribution?: string };
  baselines?: Record<string, { macro_f1: number; balanced_accuracy: number; accuracy: number; macro_f1_ci95?: number[]; class?: string }>;
  history?: { epoch: number; train_loss: number; val_macro_f1: number }[];
  gates?: { gate: string; passed: boolean; detail: string }[];
  approval: { missing_requirements: string[]; failed_evaluation_gates?: string[]; refused_because?: string[] } | null;
  lifecycle?: { state: string };
  limitations?: string[];
  test_metrics: {
    n?: number; accuracy?: number; macro_f1?: number; balanced_accuracy?: number; macro_f1_ci95?: number[]; balanced_accuracy_ci95?: number[]; ece?: number; ece_uncalibrated?: number; temperature?: number;
    per_class?: PerClass; confusion_matrix?: number[][]; classes?: string[]; warnings?: string[]; failure_cases?: unknown[];
    subgroups_by_fitzpatrick?: Record<string, { n: number; accuracy: number; balanced_accuracy: number | null; reliable: boolean }>;
    robustness?: Record<string, { macro_f1: number; balanced_accuracy: number; mean_confidence: number }>;
    selective_prediction?: { threshold: number; coverage: number; accuracy_on_accepted: number | null }[];
    ood?: { n: number; mean_confidence: number; fraction_above_threshold: Record<string, number> };
  };
};

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : (v * 100).toFixed(1) + "%");
const ci = (c?: number[]) => (c ? ` (95% CI ${c[0]}–${c[1]})` : "");

export default function ModelCard({ m }: { m: Model }) {
  const t = m.test_metrics ?? {};
  const evaluated = typeof t.n === "number";
  const classes = t.classes ?? m.classes ?? [];
  const how = [m.architecture, m.finetune && `fine-tune: ${m.finetune}`, m.epochs && `${m.epochs} epochs`, m.device, m.dataset_version !== undefined && `dataset v${m.dataset_version}`, m.training_seconds && `${Math.round(m.training_seconds / 60)} min`].filter(Boolean).join(" · ");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={m.status} />{m.lifecycle?.state && <Pill tone="info">Lifecycle: {m.lifecycle.state.replaceAll("_", " ")}</Pill>}<Pill tone="warn">Not clinically validated</Pill>
        <span className="text-sm text-muted">{how}</span></div>

      {!evaluated && <Note tone="warn">No evaluation has been run on this model here, so there are no results to show. It cannot be approved or used for patients until it has been evaluated.</Note>}

      {evaluated && <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Held-out test (n={t.n})</th><th>Macro-F1</th><th>Balanced acc.</th><th>Accuracy</th></tr></thead><tbody>
        <tr><th>This model</th><td>{t.macro_f1}{ci(t.macro_f1_ci95)}</td><td>{t.balanced_accuracy}{ci(t.balanced_accuracy_ci95)}</td><td>{t.accuracy}</td></tr>
        {Object.entries(m.baselines ?? {}).map(([k, b]) => <tr key={k}><th>Baseline: {k.replaceAll("_", " ")}</th><td>{b.macro_f1}{ci(b.macro_f1_ci95)}</td><td>{b.balanced_accuracy}</td><td>{b.accuracy}</td></tr>)}
      </tbody></table></div>}

      {m.gates && (
        <div className="rounded-2xl bg-ivory p-3"><h3 className="mb-2 font-sans !text-base">Release gates (evaluation)</h3>
          <ul className="space-y-1 text-sm">{m.gates.map((g) => <li key={g.gate} className="flex items-start gap-2">{g.passed ? <Check className="mt-0.5 h-4 w-4 text-[var(--good-ink)]" aria-label="passed" /> : <X className="mt-0.5 h-4 w-4 text-[var(--bad-ink)]" aria-label="failed" />}<span><strong>{g.gate.replaceAll("_", " ")}</strong> — {g.detail}</span></li>)}</ul>
          <p className="mt-2 text-xs text-muted">Passing evaluation gates is necessary, not sufficient: approval also needs clinical sign-off, subgroup documentation, an intended-use statement and regulatory review.</p></div>
      )}
      {(t.warnings ?? []).map((w) => <Note key={w} tone="warn">{w}</Note>)}

      {evaluated && <Details title="Per-class results (sensitivity matters most for serious classes)"><div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Class</th><th>n</th><th>Sensitivity</th><th>Specificity</th><th>Precision</th><th>AUROC</th><th>Missed (FN)</th></tr></thead><tbody>
        {Object.entries(t.per_class ?? {}).map(([c, v]) => <tr key={c}><th>{c}</th><td>{v.n}</td><td>{pct(v.sensitivity)}</td><td>{pct(v.specificity)}</td><td>{pct(v.precision)}</td><td>{v.auroc ?? "—"}</td><td>{v.false_negatives}</td></tr>)}</tbody></table></div>
        {t.confusion_matrix && <div className="mt-3 overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>true \\ predicted</th>{classes.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>
          {t.confusion_matrix.map((row, i) => <tr key={i}><th>{classes[i]}</th>{row.map((v, j) => <td key={j} style={{ background: i === j ? "var(--good-bg)" : v ? "var(--bad-bg)" : undefined }}>{v}</td>)}</tr>)}</tbody></table></div>}</Details>}

      {t.subgroups_by_fitzpatrick && <Details title="Skin-tone subgroups (Fitzpatrick; most images have no label)"><div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Group</th><th>n</th><th>Accuracy</th><th>Balanced acc.</th><th>Reliable?</th></tr></thead><tbody>
        {Object.entries(t.subgroups_by_fitzpatrick).map(([g, v]) => <tr key={g}><th>{g}</th><td>{v.n}</td><td>{pct(v.accuracy)}</td><td>{pct(v.balanced_accuracy)}</td><td>{v.reliable ? "yes" : "no — too few images"}</td></tr>)}</tbody></table></div>
        <p className="mt-2 text-sm text-muted">Types 4–6 are extremely under-represented. No claim can be made for darker skin tones.</p></Details>}

      {t.robustness && <Details title="Robustness & calibration"><p className="mb-2 text-sm">Calibration (ECE): {t.ece_uncalibrated} before → {t.ece} after temperature scaling (T={t.temperature}, fitted on validation).</p><div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Perturbation</th><th>Macro-F1</th><th>Balanced acc.</th><th>Mean confidence</th></tr></thead><tbody>
        {Object.entries(t.robustness).map(([k, v]) => <tr key={k}><th>{k}</th><td>{v.macro_f1}</td><td>{v.balanced_accuracy}</td><td>{v.mean_confidence}</td></tr>)}</tbody></table></div>
        {t.selective_prediction && <div className="mt-3 overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Abstain below confidence</th><th>Coverage</th><th>Accuracy on accepted</th></tr></thead><tbody>{t.selective_prediction.map((s) => <tr key={s.threshold}><th>{s.threshold}</th><td>{pct(s.coverage)}</td><td>{pct(s.accuracy_on_accepted)}</td></tr>)}</tbody></table></div>}
        {t.ood && <p className="mt-3 text-sm">Out-of-distribution images (n={t.ood.n}, not skin lesions): mean confidence {t.ood.mean_confidence}; share still above each threshold: {Object.entries(t.ood.fraction_above_threshold).map(([k, v]) => `${k}: ${pct(v)}`).join(" · ")}.</p>}</Details>}

      <Details title="Provenance & reproducibility"><table><tbody>
        <tr><th>Dataset licence</th><td>{m.dataset_license?.official_license_name ?? m.dataset_license?.spdx ?? "—"} <span className="text-muted">({m.dataset_license?.source})</span></td></tr>
        <tr><th>Splits (images / patients)</th><td>{Object.entries(m.split_sizes ?? {}).map(([k, v]) => `${k}: ${v}/${m.patients_per_split?.[k] ?? "?"}`).join(" · ")}</td></tr>
        <tr><th>Near-duplicates vs train</th><td>val {m.near_duplicates_vs_train?.val} · test {m.near_duplicates_vs_train?.test}</td></tr>
        <tr><th>Pretrained weights</th><td className="break-all">{m.pretrained_weights} <span className="text-muted">sha256 {m.pretrained_sha256?.slice(0, 16)}… {m.pretrained_provenance}</span></td></tr>
        <tr><th>Code revision</th><td>{m.code_revision}</td></tr><tr><th>Artifact sha256</th><td className="break-all">{m.artifact_sha256}</td></tr></tbody></table>
        {m.limitations && <ul className="mt-2 list-disc pl-5 text-sm">{m.limitations.map((l) => <li key={l}>{l}</li>)}</ul>}</Details>
    </div>
  );
}
