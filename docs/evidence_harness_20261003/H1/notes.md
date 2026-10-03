Prefer simple, leak-resistant baselines over elaborate stacking/search.

Validation discipline:
- Treat any validation ROC AUC far above a plausible baseline with suspicion, especially if it is computed on training predictions, stacked meta-features fitted on the same rows, or after heavy tuning.
- For stacking, only report out-of-fold validation from a fully nested procedure. Do not score the meta-model on the same training rows it was fit on.
- If a model shows extremely high CV/train AUC, assume leakage or evaluation error and fall back to a simpler pipeline.
- When choosing a submission candidate, prefer an unflagged node with believable validation over a flagged node with a much higher score.

Implementation discipline:
- Keep preprocessing inside the cross-validation pipeline when it depends on labels or selected features.
- Favor 3-5 fold CV with one straightforward model family (e.g. TF-IDF + logistic regression / linear model). Avoid nested RandomizedSearchCV inside outer CV unless the search space is tiny.
- Under time limits, skip expensive hyperparameter search, large ensembles, and repeated conversion of sparse text matrices to dense arrays.
- For text + numeric features, keep text features sparse when possible and use consistent feature names/types.
- If combining pandas DataFrames from different sources, ensure all column names have the same type; converting all columns to strings is acceptable.

Before finalizing code:
- Verify the validation metric is computed on held-out folds only.
- Verify submission selection favors believable CV over eye-catching but suspect scores.
