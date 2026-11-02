# Part 1 - Foundation: data model, auth, apply for leave

**Goal:** a signed-in employee can see balances and submit a request. Quota and overlap rules are enforced.
The approval workflow arrives in Part 2, so for now a valid request simply waits in `PENDING_MANAGER`.

## What gets built
| Piece | Where |
|---|---|
| 4 DynamoDB tables + 2 GSIs on requests, 1 GSI on employees | `infra/modules/part1_foundation/main.tf`, [schema](../schema/dynamodb_schema.md) |
| Cognito user pool, web client, groups `Employee` / `Manager` / `HRAdmin` | same file |
| HTTP API with JWT authorizer, routes `POST /leave`, `GET /me`, `/balances`, `/requests` | same file |
| `submit_leave`, `api_read` Lambdas and `common.py` | `backend/` |
| SES sender identity | same file |
| Rules live in the **config table**, not code | `schema/seed/leave_config.json` |

## Setup
```bash
cd infra
terraform init
terraform apply -target=module.part1 -var enable_workflow=false \
  -var sender_email=you@gmail.com -var hr_email=you+emp200@gmail.com
# click the SES verification link sent to you@gmail.com

cd .. && pip install boto3
python scripts/seed.py --email-base you@gmail.com --verify-ses
```

## How the submit logic works (`backend/submit_leave.py`)
1. **Validate**: known leave type, end >= start, same year, weekdays only counted, only SICK may be back-dated, employee has a manager.
2. **Overlap check**: query this employee's requests with status pending or approved whose dates intersect. Overlap -> `409`.
3. **Quota check**: `available = total_quota + carried_forward - used - pending`. If the request is bigger, store it as `AUTO_REJECTED` with a reason and email the employee.
4. **Reserve**: otherwise add the days to `pending` (not `used`) using an optimistic-lock `version` check, so two parallel requests cannot both take the last days.
5. Save the request; in Part 2 this step also starts Step Functions.

## Test it (curl)
```bash
cd infra
API=$(terraform output -raw api_endpoint); CLIENT=$(terraform output -raw client_id)
TOKEN=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id $CLIENT \
  --auth-parameters USERNAME=you+emp001@gmail.com,PASSWORD='Leave@12345' \
  --query AuthenticationResult.IdToken --output text)

curl -s -H "Authorization: $TOKEN" $API/balances | python -m json.tool

curl -s -X POST $API/leave -H "Authorization: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"leave_type":"CASUAL","start_date":"2026-11-16","end_date":"2026-11-17","reason":"test"}'
```
Then run the same POST again (expect `409` overlap) and submit `SICK` from `2026-11-02` to `2026-11-20` (15 days vs 12 quota: expect `AUTO_REJECTED`).
Unit tests: `AWS_DEFAULT_REGION=ap-south-1 pytest -q tests`

## Checklist before Part 2
- [ ] `/balances` returns four leave types for EMP001
- [ ] First CASUAL request is created and `pending` rises by 2 in the balances table
- [ ] Repeating it returns an overlap error
- [ ] The 15-day SICK request is auto-rejected with a reason
- [ ] You can change `annual_quota` in the config table and a **new** employee sees the new quota (no deploy)
