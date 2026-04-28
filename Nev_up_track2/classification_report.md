# NevUp Track 2 — Pathology Classification Eval

**Accuracy:** 100.00% on the 10-trader seed dataset.

## Per-trader predictions

| Trader | Truth | Predicted | Correct | Top score |
| --- | --- | --- | --- | --- |
| Alex Mercer | `revenge_trading` | `revenge_trading` | ✅ | 1.000 |
| Jordan Lee | `overtrading` | `overtrading` | ✅ | 1.000 |
| Sam Rivera | `fomo_entries` | `fomo_entries` | ✅ | 1.000 |
| Casey Kim | `plan_non_adherence` | `plan_non_adherence` | ✅ | 1.000 |
| Morgan Bell | `premature_exit` | `premature_exit` | ✅ | 0.837 |
| Taylor Grant | `loss_running` | `loss_running` | ✅ | 1.000 |
| Riley Stone | `session_tilt` | `session_tilt` | ✅ | 0.786 |
| Drew Patel | `time_of_day_bias` | `time_of_day_bias` | ✅ | 1.000 |
| Quinn Torres | `position_sizing_inconsistency` | `position_sizing_inconsistency` | ✅ | 1.000 |
| Avery Chen | `control` | `control` | ✅ | 0.081 |

## sklearn classification_report

```
precision    recall  f1-score   support

                      control       1.00      1.00      1.00         1
                 fomo_entries       1.00      1.00      1.00         1
                 loss_running       1.00      1.00      1.00         1
                  overtrading       1.00      1.00      1.00         1
           plan_non_adherence       1.00      1.00      1.00         1
position_sizing_inconsistency       1.00      1.00      1.00         1
               premature_exit       1.00      1.00      1.00         1
              revenge_trading       1.00      1.00      1.00         1
                 session_tilt       1.00      1.00      1.00         1
             time_of_day_bias       1.00      1.00      1.00         1

                     accuracy                           1.00        10
                    macro avg       1.00      1.00      1.00        10
                 weighted avg       1.00      1.00      1.00        10
```