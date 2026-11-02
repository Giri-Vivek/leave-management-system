# Edge-case report

Fill the **Evidence** column with a screenshot filename from `docs/screenshots/`.

| # | Scenario | How to reproduce | Expected result | Evidence |
|---|---|---|---|---|
| 1 | Insufficient balance | EMP001 requests SICK 2026-11-02 to 2026-11-20 (15 days, quota 12) | Status `AUTO_REJECTED`, reason "requested 15 day(s), only 12 available", email sent, `pending` unchanged | |
| 2 | Overlapping dates | Submit CASUAL 16-17 Nov, then SICK 17-18 Nov | Second call returns 409 naming the clashing request; no workflow starts | |
| 3 | Manager inaction (48h) | Deploy with `approval_timeout_seconds=120`; submit; do nothing | After timeout: status `ESCALATED`, employee emailed, HR emailed with links. If HR also ignores it: `EXPIRED`, days released, employee emailed | |
| 4 | Long leave needs HR | Submit 6+ working days; manager approves | Second email goes to HR; balance changes only after HR approves | |
| 5 | Link used twice | Click an approve link, then click it again | "Already handled" page; no second state change | |
| 6 | Tampered or expired link | Edit one character of the token | 403 "not valid" (or 410 if expired) | |
| 7 | Two requests race for the last days | Balance 3: send two 3-day requests at the same time | One succeeds, the other is auto-rejected (optimistic lock) | |
| 8 | Weekend-only dates | Request Sat-Sun | 400 "only contain weekends" | |
| 9 | Request spans two years | 29 Dec to 5 Jan | 400, submit one per year | |
| 10 | Back-dated non-sick leave | CASUAL starting yesterday | 400 "Only sick leave can be back-dated" | |
| 11 | Employee with no manager | Remove `manager_id` | 400 "No approving manager is set" | |
| 12 | Rejected after manager approval | Manager approves 6+ days, HR rejects | `REJECTED`, `pending` released, `used` unchanged | |
