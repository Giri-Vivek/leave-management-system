# Leave Management System (serverless, AWS)

Employees apply for leave, managers approve through a signed email link, long leaves need HR as well,
quotas are enforced automatically, and managers/HR get a live absence calendar.

```
Browser (S3 + CloudFront) -- Cognito login --> API Gateway (JWT authorizer)
                                                   |
        +------------------+----------------------+------------------+
        v                  v                      v                  v
  submit_leave        api_read             reports_api        decision_handler (signed link)
        |                                                            |
        +--> Step Functions --> notify_approver (SNS + SES, waits) --+
                    |--> workflow_actions (escalate / approve / reject / expire)
                    v
  DynamoDB: leave-config, employees, leave-balances, leave-requests
  EventBridge (weekly) --> carry_forward --> balances + SES summary
```

## The project in three parts

| Part | You build | Folder / files | Guide |
|---|---|---|---|
| **1. Foundation** | DynamoDB tables, Cognito, HTTP API, apply-for-leave with quota + overlap checks, balances and history | `infra/modules/part1_foundation`, `backend/common.py`, `submit_leave.py`, `api_read.py`, `schema/` | [docs/part1-foundation.md](docs/part1-foundation.md) |
| **2. Approval workflow** | Step Functions state machine, SNS + SES emails, signed approve/reject links, 48h escalation, balance settlement, weekly carry-forward | `infra/modules/part2_workflow`, `statemachine/`, `notify_approver.py`, `decision_handler.py`, `workflow_actions.py`, `carry_forward.py` | [docs/part2-workflow.md](docs/part2-workflow.md) |
| **3. Frontend and reports** | Employee view, manager/HR calendar, pending list, CSV report, S3 + CloudFront hosting, demo and deliverables | `infra/modules/part3_frontend`, `frontend/`, `reports_api.py` | [docs/part3-frontend.md](docs/part3-frontend.md) |

Deliverable documents: [balance logic](docs/balance-logic.md) and [edge-case report](docs/edge-cases.md).
Do the parts in order; each guide ends with a checklist that tells you it works before you move on.

## Prerequisites
- AWS account and the AWS CLI configured (`aws configure`), region `ap-south-1` (Mumbai) by default
- Terraform 1.5+ and Python 3.12 (`pip install boto3 pytest`)
- One email address you can read. Gmail plus-addressing (`you+emp001@gmail.com`) lets you play every role from one inbox.
- **SES starts in sandbox mode:** it only sends to verified addresses. The seed script verifies them for you.

## Quick start (all three parts at once)
```bash
cd infra
terraform init
terraform apply -var sender_email=you@gmail.com -var hr_email=you+emp200@gmail.com
#   -> click the SES verification link that arrives for you@gmail.com

cd ..
pip install boto3
python scripts/seed.py --email-base you@gmail.com --verify-ses     # config, employees, logins
#   -> click each SES verification link (4 emails, all in your inbox)
./scripts/deploy_frontend.sh                                        # prints the website URL
```
Sign in with `you+emp001@gmail.com` / `Leave@12345` (employee), `+emp100` (manager), `+emp200` (HR admin).

Cleanup: `cd infra && terraform destroy`.

## Repo layout
```
backend/        Python Lambdas, one flat package (common.py is shared)
statemachine/   leave_approval.asl.json  (Amazon States Language)
schema/         dynamodb_schema.md + seed/*.json
infra/          Terraform root + modules/part1_foundation, part2_workflow, part3_frontend
frontend/       index.html, styles.css, app.js
scripts/        seed.py, deploy_frontend.sh
tests/          pure-logic unit tests
docs/           part guides, balance logic, edge-case report
```

## Known limits (good "future work" for your report)
No public-holiday calendar or half-days (see `working_days()` in `common.py`); one HR mailbox; API CORS is `*`
(restrict to the CloudFront URL for production); sessions last the 1-hour ID-token lifetime; a request cannot span two calendar years.
