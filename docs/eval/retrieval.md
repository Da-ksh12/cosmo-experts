| Method | Hit@1 | Hit@3 | MRR | AUROC (answerable vs not) | Threshold @ FPR<=5% | Answer recall | Answer precision | Wrongly answered |
|---|---|---|---|---|---|---|---|---|
| lexical_tfidf | 0.8 | 0.871 | 0.839 | 0.973 | 0.166 | 0.907 | 1.0 | 0/120 |
| dense_bge | 0.986 | 1.0 | 0.99 | 0.994 | 0.676 | 0.957 | 1.0 | 0/120 |
| hybrid_0.3_dense | 0.821 | 0.914 | 0.877 | 0.99 | 0.301 | 0.907 | 1.0 | 0/120 |
| hybrid_0.5_dense | 0.893 | 0.986 | 0.939 | 0.997 | 0.372 | 0.986 | 1.0 | 0/120 |
| hybrid_0.7_dense | 0.9 | 1.0 | 0.945 | 0.996 | 0.472 | 0.986 | 1.0 | 0/120 |
