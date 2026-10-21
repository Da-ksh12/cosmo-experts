"""Retrieval and abstention evaluation over the dermatology education pack (app/derm/reference.py).

Corpus: the shipped pack (unreviewed). Queries: eval/derm_queries.py, hand-written, in-scope (with the gold article) and out-of-scope (far and near-domain). The development queries choose the
abstention threshold for each retriever mode; the held-out queries are then scored once at that choice. Evidence class: synthetic_fixture. This says how the retrieval mechanism behaves on a small
written question set; it says nothing about the clinical content of any article and nothing about real patient wording.

    python eval/derm_retrieval_eval.py [--out ../docs/eval]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.ai.evaluation import common  # noqa: E402
from app.assistant.retrieval import HybridRetriever, SentenceTransformerEmbedder  # noqa: E402
from app.derm import pack, reference  # noqa: E402
from eval import derm_queries as Q  # noqa: E402

MAX_FPR = 0.05          # out-of-scope questions wrongly answered, at most, on the development set


def corpus() -> list[dict]:
    pk = pack.load()
    pool: list[dict] = []
    for a in pk.articles.values():
        pool += reference.passages_of(a.id, a.title, a.summary, a.sections)
    return pool


def top(ret: HybridRetriever, pool: list[dict], q: str, k: int = 3):
    return [(pool[h.index], h.score) for h in ret.search(q, pool, k=k)]


def score_set(ret, pool, in_scope, far, near) -> dict:
    rows_in = []
    for q, gold in in_scope:
        hits = top(ret, pool, q)
        arts = []
        for p, _ in hits:
            if p["article_id"] not in arts:
                arts.append(p["article_id"])
        rank = arts.index(gold) if gold in arts else None
        p1, s1 = hits[0]
        rows_in.append({"q": q, "gold": gold, "score": s1, "top1": p1["article_id"], "rank": rank, "supported": p1["article_id"] == gold})
    rows_out = []
    for kind, qs in (("far", far), ("near", near)):
        for q in qs:
            p1, s1 = top(ret, pool, q, 1)[0]
            rows_out.append({"q": q, "kind": kind, "score": s1, "top1": p1["article_id"]})
    return {"in": rows_in, "out": rows_out}


def metrics_at(raw: dict, t: float) -> dict:
    ins, outs = raw["in"], raw["out"]
    n, m = len(ins), len(outs)
    answered = [r for r in ins if r["score"] >= t]
    wrong = [r for r in outs if r["score"] >= t]
    correct = [r for r in answered if r["supported"]]
    from sklearn.metrics import roc_auc_score
    y = np.r_[np.ones(n), np.zeros(m)]
    s = np.r_[[r["score"] for r in ins], [r["score"] for r in outs]]
    return {"n_in_scope": n, "n_out_of_scope": m, "hit@1": round(sum(r["rank"] == 0 for r in ins) / n, 3), "hit@3": round(sum(r["rank"] is not None for r in ins) / n, 3),
            "AUROC_answerable_vs_not": round(float(roc_auc_score(y, s)), 3), "threshold": round(t, 4), "answered_in_scope": f"{len(answered)}/{n}",
            "answered_with_right_article": f"{len(correct)}/{n}", "correct_answer_rate": round(len(correct) / n, 3), "correct_answer_rate_ci95": common.wilson(len(correct), n),
            "out_of_scope_wrongly_answered": f"{len(wrong)}/{m}", "false_answer_rate": round(len(wrong) / m, 3), "false_answer_rate_ci95": common.wilson(len(wrong), m),
            "wrongly_answered_near": sum(r["kind"] == "near" for r in wrong), "wrongly_answered_far": sum(r["kind"] == "far" for r in wrong),
            "wrong_queries": [r["q"] for r in wrong], "answered_with_wrong_article": [r["q"] for r in answered if not r["supported"]]}


def choose_threshold(dev: dict) -> float:
    """Highest correct-answer rate on the development set subject to false answers <= MAX_FPR; ties go to the higher (stricter) threshold."""
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
    pool = corpus()
    results = {}
    for rname, ret in retrievers.items():
        dev = score_set(ret, pool, Q.DEV_IN, Q.DEV_OUT_FAR, Q.DEV_OUT_NEAR)
        held = score_set(ret, pool, Q.HELDOUT_IN, Q.HELDOUT_OUT_FAR, Q.HELDOUT_OUT_NEAR)
        t = round(choose_threshold(dev), 4)
        results[rname] = {"retriever_mode": ret.mode, "passages": len(pool), "development": metrics_at(dev, t), "held_out": metrics_at(held, t)}
        r = results[rname]
        print(f"{rname:10s} t={t:.4f} | dev right {r['development']['correct_answer_rate']} FPR {r['development']['false_answer_rate']} | "
              f"held-out right {r['held_out']['correct_answer_rate']} FPR {r['held_out']['false_answer_rate']} AUROC {r['held_out']['AUROC_answerable_vs_not']}", flush=True)
    pk = pack.load()
    report = common.header("derm_retrieval", "synthetic_fixture",
                           {"max_dev_false_answer_rate": MAX_FPR, "retrievers": list(retrievers), "embedder": emb.name if emb.available else None, "passage_unit": "one statement with its article title"},
                           dataset={"corpus": "content/derm (unreviewed pack)", "pack_sha256": pk.sha256, "queries": {"dev_in": len(Q.DEV_IN), "dev_out": len(Q.DEV_OUT_FAR) + len(Q.DEV_OUT_NEAR),
                                                                                                                  "heldout_in": len(Q.HELDOUT_IN), "heldout_out": len(Q.HELDOUT_OUT_FAR) + len(Q.HELDOUT_OUT_NEAR)}},
                           notes=["Queries are hand-written by one author; the held-out set is not independent of the author or the system design.",
                                  "The corpus is unreviewed text. Nothing here validates the clinical content of any article.",
                                  "Thresholds were chosen on the development set only and applied once to the held-out set. The keyword router answers the easy questions before retrieval is used."])
    report["results"] = results
    out = Path(a.out)
    common.write_report(out / "derm_retrieval.json", report)
    print(f"wrote {out / 'derm_retrieval.json'}")


if __name__ == "__main__":
    main()
