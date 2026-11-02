# Balance logic walkthrough

## 1. Where the rules live
`leave_type` rows in the **leave-config** table: `annual_quota`, `carry_forward_allowed`, `max_carry_forward`,
`requires_hr_above_days`, `is_unlimited`. HR edits these rows; no Lambda is redeployed.
A year's balance row is created lazily the first time an employee is touched, copying the quota from config.

## 2. The three counters
| Counter | Meaning | Changes when |
|---|---|---|
| `used` | Days actually taken | **Only** on final approval |
| `pending` | Days held for requests still in the workflow | Submission (+), final decision (-) |
| `carried_forward` | Days brought from last year | Weekly job, once |

`available = total_quota + carried_forward - used - pending`

`pending` is what prevents double counting: if an employee has 5 days left and files two 4-day requests, the second sees
`available = 1` and is rejected, even though neither is approved yet. `used` stays untouched until approval, as required.

## 3. Lifecycle of a 3-day request (balance 10)
| Event | used | pending | available |
|---|---|---|---|
| Start | 0 | 0 | 10 |
| Submitted | 0 | 3 | 7 |
| Approved (final) | 3 | 0 | 7 |
| *or* Rejected / expired / failed | 0 | 0 | 10 |

## 4. Safety rules
- **Optimistic locking:** every balance row has a `version`. Reserving days reads the row, then writes with `version = <read value>`. If another request changed it first the write fails and is retried, so two requests cannot both take the last days.
- **One transaction on final decisions:** request status and balance change are written with `TransactWriteItems`. Either both happen or neither does, so a request can never be APPROVED without its days moving from `pending` to `used`.
- **Idempotent:** finishing an already-finished request does nothing, so Step Functions retries are harmless.
- **Final re-check:** on approval, if `used` would exceed `total + carried_forward - pending` (HR lowered the quota mid-flight) the approval fails and the request goes to `FAILED`, releasing the days.

## 5. Unlimited types (UNPAID)
`is_unlimited = true` skips the quota check and the `pending` reservation. `used` still increases on approval for reporting.

## 6. Overlaps
The same employee cannot hold two requests (pending or approved) whose date ranges intersect:
`new_start <= existing_end AND new_end >= existing_start`.

## 7. Weekly job
Runs Mondays. For each employee and each type with `carry_forward_allowed`: carry = `min(last year's available, max_carry_forward)`,
written once (guarded by `cf_processed`). Then it emails a balance summary. Re-running it changes nothing.
