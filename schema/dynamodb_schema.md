# DynamoDB schema

All tables are on-demand (PAY_PER_REQUEST). Names are prefixed with the project name (default `leave-mgmt`).

## leave-mgmt-leave-config   (HR edits rows here - no code change needed)
| Attribute | Type | Notes |
|---|---|---|
| **leave_type** (PK) | S | `SICK`, `CASUAL`, `EARNED`, `UNPAID` |
| annual_quota | N | Days granted per year |
| carry_forward_allowed | BOOL | |
| max_carry_forward | N | Cap on days carried into next year |
| requires_hr_above_days | N | Requests longer than this need HR after the manager |
| is_unlimited | BOOL | `true` = no quota check (UNPAID) |
| description | S | Shown in the UI |

## leave-mgmt-employees
| Attribute | Type | Notes |
|---|---|---|
| **employee_id** (PK) | S | |
| name, email, role | S | `email` stored lowercase; role = Employee / Manager / HRAdmin |
| manager_id | S | Who approves this person's leave |

GSI `email-index` (PK `email`) - maps the Cognito login to an employee record.

## leave-mgmt-leave-balances   (one row per employee, per leave type, per year)
| Attribute | Type | Notes |
|---|---|---|
| **employee_id** (PK) | S | |
| **leave_type_year** (SK) | S | e.g. `CASUAL#2026` |
| total_quota | N | Copied from config when the row is created |
| carried_forward | N | Credited by the weekly job |
| used | N | **Only changes on final approval** |
| pending | N | Days held by requests still in the workflow |
| version | N | Optimistic-lock counter (bumped on every write) |
| cf_processed | BOOL | Makes the carry-forward job idempotent |

`available = total_quota + carried_forward - used - pending`

## leave-mgmt-leave-requests
| Attribute | Type | Notes |
|---|---|---|
| **employee_id** (PK) | S | |
| **request_id** (SK) | S | `<epoch-ms>-<8 hex>` - sorts by time |
| leave_type, start_date, end_date, days, reason | | dates are ISO `YYYY-MM-DD` |
| status | S | `PENDING_MANAGER` `ESCALATED` `PENDING_HR` (active) · `APPROVED` `REJECTED` `AUTO_REJECTED` `EXPIRED` `FAILED` (final) |
| manager_id, requires_hr | | set at submission |
| task_token, awaiting | S | Step Functions token while paused (removed when decided) |
| execution_arn | S | Link to the Step Functions execution (for screenshots) |
| reject_reason, decided_at, created_at | | |

GSIs
- `manager-status-index` (PK `manager_id`, SK `status`) - manager's pending list
- `status-start-index` (PK `status`, SK `start_date`) - HR pending list and the absence calendar
