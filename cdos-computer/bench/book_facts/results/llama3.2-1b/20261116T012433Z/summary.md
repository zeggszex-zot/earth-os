# Book-facts bench: llama3.2:1b (baf6a787fdff)

Sample `sha256:a3bed43dba00…`, prompt `sha256:bdaf3a318fbc…`, options {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 512}.

| | |
|---|---|
| paragraphs | 50 |
| fact lines | 1200 (24.0 a paragraph) |
| distinct within their paragraph (the scorecard's rows) | 486 |
| malformed (automatic check) | 98 |
| paragraphs with no fact lines | 0 |
| outputs cut off at the length cap | 22 |
| speed | 6.1 tokens/s median; 53.9 s a paragraph; 3602.6 s in all |
| same output when paragraph 1 is asked again | True |

Nothing marked yet: 486 facts in scorecard.tsv.

Model output only: nothing here is in corpus.pf; review.pf is for the review queue.
