# Part 2 - Approval workflow: Step Functions, email, signed links, carry-forward

**Goal:** every request moves through manager (and HR for long leave) approval with an email at each stage change,
and the balance is settled exactly once, on final approval.

## What gets built
| Piece | Where |
|---|---|
| State machine definition | [statemachine/leave_approval.asl.json](../statemachine/leave_approval.asl.json) |
| `notify_approver` - emails approve/reject links, publishes SNS, **pauses** the workflow | `backend/notify_approver.py` |
| `decision_handler` - public `/decision` route that checks the signature and resumes the workflow | `backend/decision_handler.py` |
| `workflow_actions` - escalate, final approve/reject, expire, fail (all idempotent) | `backend/workflow_actions.py` |
| `carry_forward` - weekly EventBridge job | `backend/carry_forward.py` |
| Terraform: secret, SNS topic, 4 Lambdas, state machine, EventBridge rule | `infra/modules/part2_workflow/main.tf` |

## The state machine
```
NotifyManager (waits for click, timeout = 48h)
   |-- timeout --> EscalateToHR --> NotifyHR
   |-- approved --> requires_hr? --yes--> NotifyHR (timeout --> ExpireRequest)
   |                      |--no--> FinalApprove
   '-- rejected --> FinalReject
NotifyHR --> approved: FinalApprove / rejected: FinalReject
any unexpected error --> MarkFailed (releases the reserved days)
```
`requires_hr` is decided at submission from `requires_hr_above_days` in the config table (default: more than 5 days).

## Why the links are safe
- Token = `employee|request|stage|action|expiry`, signed with HMAC-SHA256 using a key in Secrets Manager. Edited links fail (403), old links fail (410).
- The email link only opens a **confirmation page**; the decision happens on a POST. Mail scanners that pre-fetch links therefore cannot approve anything.
- The Step Functions task token is stored on the request and removed with a conditional update, so a link works **once**.

## Setup
```bash
cd infra
terraform apply -var sender_email=you@gmail.com -var hr_email=you+emp200@gmail.com   # turns the workflow on
```
To demo escalation quickly add `-var approval_timeout_seconds=120`.

## Test it
1. Employee EMP001 submits 2 days of EARNED leave (use curl from Part 1 or the UI in Part 3).
2. Manager's inbox (`+emp100`) gets the email. Click **Review and approve**, then **Approve leave**.
3. Open *Step Functions -> leave-mgmt-approval -> Executions*: the graph is green. **Screenshot this.**
4. EMP001's inbox gets "submitted" then "approved". **Screenshot the approved email.**
5. Repeat with 6+ working days: after the manager approves, HR (`+emp200`) gets a second email.
6. Click the same approve link twice: second click says "Already handled".
7. Run the weekly job by hand: `aws lambda invoke --function-name leave-mgmt-carry_forward /dev/stdout`

## Checklist before Part 3
- [ ] Approve path ends with status `APPROVED`, `used` up by N, `pending` back down by N
- [ ] Reject path releases the pending days
- [ ] 6+ day request needs both manager and HR
- [ ] With a 120s timeout, an ignored request escalates to HR, then expires after another 120s
- [ ] Re-running `carry_forward` does not change balances a second time
