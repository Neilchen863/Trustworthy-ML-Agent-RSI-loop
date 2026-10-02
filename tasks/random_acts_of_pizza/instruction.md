# random-acts-of-pizza (train)

Predict whether a Reddit "Random Acts of Pizza" request receives a pizza. Metric: ROC AUC, higher is better.
About 2.9k training requests (JSON) after MLE-bench's split; CPU only. AIDE sees MLE-bench's own description.

14 fields exist in `train.json` but not in `test.json` (listed in `task.json` as `train_only_fields`).

Role `train`: each run's official score is part of the reward vector and goes into memory.
