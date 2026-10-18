"""Retrieval + abstention evaluation on SYNTHETIC patient documents with exact gold passages.

Compares lexical (TF-IDF), dense (BGE-small), and hybrid retrieval, then sweeps the abstention threshold on a mix of answerable and
unanswerable questions. Queries are written by hand (paraphrases, synonyms, abbreviations) - they are NOT drawn from real patients.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.assistant.retrieval import HybridRetriever, SentenceTransformerEmbedder  # noqa: E402
from app.documents import pipeline  # noqa: E402
from eval.doc_extraction_eval import make_case, render_pdf  # noqa: E402

KB = [  # EVALUATION-ONLY articles (generic wording); production articles require clinician approval
    ("What is a reference range?", "A reference range is the set of values the laboratory printed on your report as typical for the test it used. Different laboratories and methods can print different ranges."),
    ("Why one result is not a diagnosis", "A single test value cannot tell the full story. A doctor interprets results together with your symptoms, history, examination and other tests."),
    ("Repeating a test", "Doctors sometimes repeat a blood test to check whether a value was a one-off, because results can vary with time of day, hydration and the laboratory method."),
    ("Bringing earlier reports", "Earlier reports help your doctor compare values over time. Bring all previous reports and a list of the medicines you currently take."),
    ("Questions to ask your doctor", "Useful questions include what a result means for you specifically, whether it should be repeated, and whether it changes what treatment is suitable."),
]
SYN = {"Hemoglobin": ["haemoglobin", "Hb"], "Glucose Fasting": ["blood sugar", "fasting sugar"], "TSH": ["thyroid stimulating hormone", "thyroid"],
       "Vitamin D": ["vit D", "vitamin D level"], "Platelet Count": ["platelets"], "SGPT": ["ALT liver enzyme", "SGPT"], "Uric Acid": ["urate", "uric acid"],
       "Ferritin": ["iron stores ferritin", "ferritin"], "Creatinine": ["creatinine", "kidney marker creatinine"], "Total Cholesterol": ["cholesterol", "total cholesterol"]}
TEMPL = ["What is my {n} result?", "How is my {n}?", "{n} value please", "Tell me about {n} in my report", "What range is printed for {n}?", "Is my {n} inside the printed range?"]
KB_Q = [("What does reference range mean?", 0), ("Explain the printed normal range on my lab report", 0), ("Does one abnormal value mean I have a disease?", 1),
        ("Is a single result a diagnosis?", 1), ("Should the blood test be done again?", 2), ("Why might a result change when repeated?", 2),
        ("Should I bring my old reports?", 3), ("What should I ask my doctor?", 4), ("Which questions help in a consultation?", 4)]
UNANSWERABLE = ["Who won the cricket world cup in 1983?", "How do I cook pasta?", "What is the weather in Delhi today?", "Tell me about my MRI scan",
                "Write a poem about the sea", "What is the capital of France?", "How do I change my phone password?", "Recommend a good movie",
                "What is my blood group?", "Explain quantum computing", "How many steps did I walk yesterday?", "Show my dental X-ray results"]


def build_corpus(n_docs, rng):
    docs = []
    for i in range(n_docs):
        rows = make_case(rng, "columns")
        res = pipeline.process(render_pdf(rows, "columns", f"CITY{i:02d}"), "r.pdf")
        passages = []
        for p in res.pages:
            for line in p.text.splitlines():
                if len(line.strip()) >= 8:
                    passages.append({"text": line.strip(), "kind": "doc", "doc": i})
        for f in res.fields:
            passages.append({"text": f.source_text, "match": f"{f.name} result {f.name}", "kind": "doc", "doc": i})
        docs.append({"rows": rows, "passages": passages})
    return docs


def gold_idx(passages, row):
    return {j for j, p in enumerate(passages) if row["name"].lower() in p["text"].lower() and row["value"] in p["text"]}


def evaluate(method, retriever, docs, rng, n_q_per_doc):
    hits1 = hits3 = rr = n = 0
    ans_scores, un_scores = [], []
    for d in docs:
        pool = d["passages"] + [{"text": f"{t}. {b}", "kind": "kb"} for t, b in KB]
        for row in rng.sample(d["rows"], min(n_q_per_doc, len(d["rows"]))):
            n_ = rng.choice(SYN.get(row["name"], []) + [row["name"]])
            q = rng.choice(TEMPL).format(n=n_)
            gold = gold_idx(pool, row)
            res = retriever.search(q, pool, k=5)
            rank = next((r for r, h in enumerate(res) if h.index in gold), None)
            n += 1; hits1 += rank == 0; hits3 += rank is not None and rank < 3; rr += 1 / (rank + 1) if rank is not None else 0
            ans_scores.append(res[0].score)
        for q, ki in KB_Q:
            gold = {len(d["passages"]) + ki}
            res = retriever.search(q, pool, k=5)
            rank = next((r for r, h in enumerate(res) if h.index in gold), None)
            n += 1; hits1 += rank == 0; hits3 += rank is not None and rank < 3; rr += 1 / (rank + 1) if rank is not None else 0
            ans_scores.append(res[0].score)
        for q in UNANSWERABLE:
            un_scores.append(retriever.search(q, pool, k=1)[0].score)
    return {"n_answerable": n, "hit@1": round(hits1 / n, 3), "hit@3": round(hits3 / n, 3), "MRR": round(rr / n, 3)}, np.array(ans_scores), np.array(un_scores)


def abstention_sweep(ans, un):
    best = None
    for th in np.linspace(0, max(ans.max(), un.max()), 200):
        tp = int((ans >= th).sum()); fn = int((ans < th).sum()); fp = int((un >= th).sum()); tn = int((un < th).sum())
        prec = tp / (tp + fp) if tp + fp else 1.0; rec = tp / (tp + fn)
        f = 2 * prec * rec / (prec + rec) if prec + rec else 0
        # prefer no wrong answers to unanswerable questions: maximise F1 subject to FP rate <= 5%
        if fp / max(len(un), 1) <= 0.05 and (best is None or f > best["f1"]):
            best = {"threshold": round(float(th), 3), "answer_recall": round(rec, 3), "answer_precision": round(prec, 3), "f1": round(f, 3),
                    "unanswerable_wrongly_answered": f"{fp}/{len(un)}"}
    from sklearn.metrics import roc_auc_score
    y = np.r_[np.ones(len(ans)), np.zeros(len(un))]
    return {"AUROC_answerable_vs_not": round(float(roc_auc_score(y, np.r_[ans, un])), 3), "operating_point_FPR<=5%": best}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="../docs/eval")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    docs = build_corpus(a.docs, rng)
    emb = SentenceTransformerEmbedder()
    assert emb.available, "embedding model not downloaded"
    out = {"docs": a.docs, "seed": a.seed, "embedder": emb.name, "methods": {}}
    for name, ret in [("lexical_tfidf", HybridRetriever(None)), ("dense_bge", HybridRetriever(emb, 1.0)), ("hybrid_0.3_dense", HybridRetriever(emb, 0.3)),
                      ("hybrid_0.5_dense", HybridRetriever(emb, 0.5)), ("hybrid_0.7_dense", HybridRetriever(emb, 0.7))]:
        m, ans, un = evaluate(name, ret, docs, random.Random(a.seed + 1), 5)
        m.update(abstention_sweep(ans, un))
        out["methods"][name] = m
        print(name, json.dumps(m), flush=True)
    p = Path(a.out); p.mkdir(parents=True, exist_ok=True)
    (p / "retrieval.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    lines = ["| Method | Hit@1 | Hit@3 | MRR | AUROC (answerable vs not) | Threshold @ FPR<=5% | Answer recall | Answer precision | Wrongly answered |", "|---|---|---|---|---|---|---|---|---|"]
    for n_, m in out["methods"].items():
        o = m["operating_point_FPR<=5%"] or {}
        lines.append(f"| {n_} | {m['hit@1']} | {m['hit@3']} | {m['MRR']} | {m['AUROC_answerable_vs_not']} | {o.get('threshold')} | {o.get('answer_recall')} | {o.get('answer_precision')} | {o.get('unanswerable_wrongly_answered')} |")
    (p / "retrieval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
