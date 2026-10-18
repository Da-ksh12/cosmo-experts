"""Passage retrieval and abstention evaluation over the knowledge base (ai/knowledge.py).

Corpus: the shipped DRAFT articles (unreviewed; evaluation text only). Queries: eval/kb_queries.py, hand-written, in-scope and out-of-scope (far and near-domain). The development queries
choose the abstention threshold and the configuration; the held-out queries are then scored once at that choice. Evidence class: synthetic_fixture. This says how the retrieval mechanism
behaves on a small written query set; it says nothing about the clinical content of any article and nothing about real patient wording.

    python eval/kb_retrieval_eval.py [--out ../docs/eval]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import clinical_content  # noqa: E402
from app.ai import knowledge  # noqa: E402
from app.ai.evaluation import common  # noqa: E402
from app.assistant.retrieval import HybridRetriever, SentenceTransformerEmbedder  # noqa: E402
from eval import kb_queries as Q  # noqa: E402

MAX_FPR = 0.05          # out-of-scope questions wrongly answered, at most, on the development set
CHUNKS = {"article": 10 ** 6, "passage240": 240}


def corpus_from_drafts(max_chars: int) -> knowledge.Corpus:
    c = knowledge.Corpus()
    for a in clinical_content.kb_drafts():
        c.articles += 1
        parts = knowledge.chunk(a["body"], max_chars)
        for n, (text, s, e) in enumerate(parts):
            c.passages.append(knowledge.Passage(f"kb:{a['id']}:v1:{s}-{e}", a["id"], a["title"], 1, text, (s, e), n, len(parts), "en", "evaluation-fixture", None, None,
                                                "evaluation corpus (unreviewed draft)", "ai_draft", ""))
    return c


def top(ret: HybridRetriever, corpus: knowledge.Corpus, q: str, k: int = 3) -> list[tuple[knowledge.Passage, float]]:
    pool = [p.pool_entry() for p in corpus.passages]
    return [(corpus.passages[h.index], h.score) for h in ret.search(q, pool, k=k)]


def score_set(ret, corpus, in_scope, far, near) -> dict:
    """Raw per-query facts at every threshold-independent level: top-1 score, top-1 article, support, rank of the gold article."""
    rows_in = []
    for q, gold, support in in_scope:
        hits = top(ret, corpus, q)
        arts = [p.article_id for p, _ in hits]
        rank = next((i for i, a in enumerate(arts) if a == gold), None)
        p1, s1 = hits[0]
        supported = p1.article_id == gold and any(w.lower() in p1.text.lower() for w in support)
        rows_in.append({"q": q, "gold": gold, "score": s1, "top1": p1.article_id, "rank": rank, "supported": supported, "passage": p1.id})
    rows_out = []
    for kind, qs in (("far", far), ("near", near)):
        for q in qs:
            p1, s1 = top(ret, corpus, q, 1)[0]
            rows_out.append({"q": q, "kind": kind, "score": s1, "top1": p1.article_id, "passage": p1.id})
    return {"in": rows_in, "out": rows_out}


def metrics_at(raw: dict, t: float) -> dict:
    ins, outs = raw["in"], raw["out"]
    n, m = len(ins), len(outs)
    answered = [r for r in ins if r["score"] >= t]
    wrong = [r for r in outs if r["score"] >= t]
    correct = [r for r in answered if r["supported"]]
    mrr = sum(1 / (r["rank"] + 1) for r in ins if r["rank"] is not None) / n
    from sklearn.metrics import roc_auc_score
    y = np.r_[np.ones(n), np.zeros(m)]
    s = np.r_[[r["score"] for r in ins], [r["score"] for r in outs]]
    return {"n_in_scope": n, "n_out_of_scope": m, "hit@1": round(sum(r["rank"] == 0 for r in ins) / n, 3), "hit@3": round(sum(r["rank"] is not None for r in ins) / n, 3),
            "MRR": round(mrr, 3), "AUROC_answerable_vs_not": round(float(roc_auc_score(y, s)), 3), "threshold": round(t, 4),
            "answered_in_scope": f"{len(answered)}/{n}", "answered_with_supporting_passage": f"{len(correct)}/{n}", "correct_answer_rate": round(len(correct) / n, 3),
            "correct_answer_rate_ci95": common.wilson(len(correct), n),
            "out_of_scope_wrongly_answered": f"{len(wrong)}/{m}", "false_answer_rate": round(len(wrong) / m, 3), "false_answer_rate_ci95": common.wilson(len(wrong), m),
            "wrongly_answered_near": sum(r["kind"] == "near" for r in wrong), "wrongly_answered_far": sum(r["kind"] == "far" for r in wrong),
            "wrong_queries": [r["q"] for r in wrong], "answered_but_unsupported": [r["q"] for r in answered if not r["supported"]]}


def choose_threshold(dev: dict) -> float:
    """Highest correct-answer rate on the development set subject to false answers <= MAX_FPR; ties go to the higher (stricter) threshold. Candidates are midpoints between observed scores."""
    scores = sorted({r["score"] for r in dev["in"] + dev["out"]})
    cands = [(a + b) / 2 for a, b in zip(scores, scores[1:])] + [scores[-1] + 1e-6]
    best = None
    for t in cands:
        m = metrics_at(dev, t)
        if m["false_answer_rate"] > MAX_FPR:
            continue
        key = (m["correct_answer_rate"], t)
        if best is None or key > best[0]:
            best = (key, t)
    return best[1] if best else scores[-1] + 1e-6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/eval")
    a = ap.parse_args()
    emb = SentenceTransformerEmbedder()
    retrievers = {"lexical": HybridRetriever(None)}
    if emb.available:
        retrievers.update({"dense": HybridRetriever(emb, 1.0), "hybrid0.5": HybridRetriever(emb, 0.5)})
    results = {}
    for cname, mc in CHUNKS.items():
        corpus = corpus_from_drafts(mc)
        for rname, ret in retrievers.items():
            dev = score_set(ret, corpus, Q.DEV_IN, Q.DEV_OUT_FAR, Q.DEV_OUT_NEAR)
            held = score_set(ret, corpus, Q.HELDOUT_IN, Q.HELDOUT_OUT_FAR, Q.HELDOUT_OUT_NEAR)
            t = round(choose_threshold(dev), 4)          # the runtime uses the recorded 4-decimal value, so that is the value evaluated
            results[f"{rname}@{cname}"] = {"retriever_mode": ret.mode, "passages": len(corpus.passages), "development": metrics_at(dev, t), "held_out": metrics_at(held, t)}
            r = results[f"{rname}@{cname}"]
            print(f"{rname:10s} {cname:10s} t={t:.3f} | dev correct {r['development']['correct_answer_rate']} FPR {r['development']['false_answer_rate']} | "
                  f"held-out correct {r['held_out']['correct_answer_rate']} FPR {r['held_out']['false_answer_rate']} AUROC {r['held_out']['AUROC_answerable_vs_not']}", flush=True)
    report = common.header("kb_retrieval", "synthetic_fixture",
                           {"chunk_sizes": CHUNKS, "max_dev_false_answer_rate": MAX_FPR, "retrievers": list(retrievers), "embedder": emb.name if emb.available else None, "passage_max_chars": knowledge.PASSAGE_MAX_CHARS},
                           dataset={"corpus": "content/kb_drafts.json (unreviewed drafts, evaluation text only)", "corpus_sha256": common.sha256_json(clinical_content.kb_drafts()),
                                    "queries": {"dev_in": len(Q.DEV_IN), "dev_out": len(Q.DEV_OUT_FAR) + len(Q.DEV_OUT_NEAR), "heldout_in": len(Q.HELDOUT_IN), "heldout_out": len(Q.HELDOUT_OUT_FAR) + len(Q.HELDOUT_OUT_NEAR)}},
                           notes=["Queries are hand-written by one author; the held-out set is not independent of the author or the system design.",
                                  "The corpus is unreviewed draft text. Nothing here validates the clinical content of any article.",
                                  "Thresholds were chosen on the development set only and applied once to the held-out set."])
    report["results"] = results
    out = Path(a.out)
    common.write_report(out / "kb_retrieval.json", report)
    print(f"wrote {out / 'kb_retrieval.json'}")


if __name__ == "__main__":
    main()
