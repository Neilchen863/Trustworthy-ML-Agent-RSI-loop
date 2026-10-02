# detecting-insults-in-social-commentary (test)

Classify whether a social-media comment is an insult. Metric: ROC AUC, higher is better. Columns: `Insult`
(label, train only), `Date`, `Comment`. Apart from the label, train and test have the same columns.

## Role

`test`: the official score is never written to memory and never shown to the improver. The loop
refuses to run `improve` on a test task. Run a frozen harness on it to check transfer.
