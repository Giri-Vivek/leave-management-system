# Part 3 - Frontend, reports and final deliverables

**Goal:** a hosted site with an employee view and a manager/HR view, then the demo, documents and repo.

## What gets built
| Piece | Where |
|---|---|
| `reports_api` Lambda: `GET /calendar`, `/pending`, `/report` (managers see their team, HR sees all) | `backend/reports_api.py` |
| Private S3 bucket + CloudFront (HTTPS) | `infra/modules/part3_frontend/main.tf` |
| Static site: sign-in, balance meters, apply form, history with status badges, absence calendar, pending list, CSV download | `frontend/` |
| Deploy script that writes `config.js` from Terraform outputs and syncs to S3 | `scripts/deploy_frontend.sh` |

## Setup
```bash
cd infra && terraform apply -var sender_email=you@gmail.com -var hr_email=you+emp200@gmail.com
cd .. && ./scripts/deploy_frontend.sh        # prints https://xxxx.cloudfront.net
```
Local edits to `frontend/`: just rerun `deploy_frontend.sh` (caching is disabled, so changes show immediately).

## How the frontend works
- Sign-in calls Cognito `InitiateAuth` directly over `fetch` (no libraries). The ID token goes in the `Authorization` header.
- Role comes from the `cognito:groups` claim. The **Team** tab only appears for `Manager` / `HRAdmin`, and the API enforces the same rule server-side.
- Calendar: FullCalendar reads approved leave for each visible month. Managers see their team, HR sees everyone.
- Balance meters draw one square per day: solid = used, hatched = pending, empty = free.

## Final demo script (the required deliverable)
1. Sign in as `emp001`, show balances, apply for leave.
2. Open the manager email, approve it.
3. Step Functions execution screenshot.
4. SES "approved" email screenshot.
5. Sign in as `emp100`: calendar shows the leave; download the CSV report.
6. Show the three edge cases from [edge-cases.md](edge-cases.md).

## Repo checklist
- [ ] `schema/dynamodb_schema.md` and `statemachine/leave_approval.asl.json` committed
- [ ] `docs/balance-logic.md` and `docs/edge-cases.md` filled with your screenshots
- [ ] Screenshots in `docs/screenshots/`
- [ ] No secrets committed (`config.js`, `.terraform`, `build/` are git-ignored)
