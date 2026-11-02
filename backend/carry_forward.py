"""EventBridge (weekly). Two jobs, both idempotent:
 1. Make sure this year's balance rows exist and credit carry-forward ONCE per employee/type.
 2. Email every employee a balance summary."""
from datetime import date

from botocore.exceptions import ClientError

from common import (available, balance_key, email_html, get_or_create_balance, list_configs,
                    scan_all, send_email, table)


def credit_carry_forward(eid, cfg, year, amount):
    try:
        table("BALANCES_TABLE").update_item(
            Key=balance_key(eid, cfg["leave_type"], year),
            UpdateExpression="SET carried_forward = :c, cf_processed = :t ADD version :one",
            ConditionExpression="attribute_not_exists(cf_processed)",     # <- makes re-runs harmless
            ExpressionAttributeValues={":c": amount, ":t": True, ":one": 1})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def handler(event, context):
    year = date.today().year
    configs, sent = list_configs(), 0
    for emp in scan_all("EMPLOYEES_TABLE"):
        rows = []
        for cfg in configs:
            bal = get_or_create_balance(emp["employee_id"], cfg, year)
            if cfg.get("carry_forward_allowed") and not bal.get("cf_processed"):
                prev = table("BALANCES_TABLE").get_item(
                    Key=balance_key(emp["employee_id"], cfg["leave_type"], year - 1)).get("Item")
                carry = 0
                if prev:
                    leftover = available(prev)
                    carry = max(0, min(leftover, int(cfg.get("max_carry_forward", 0))))
                credit_carry_forward(emp["employee_id"], cfg, year, carry)
                bal = get_or_create_balance(emp["employee_id"], cfg, year)
            if not cfg.get("is_unlimited"):
                rows.append((cfg["leave_type"], int(bal["total_quota"]) + int(bal["carried_forward"]),
                             int(bal["used"]), int(bal["pending"]), available(bal)))
        details = {t: f"{a} left (of {tot}, used {u}, pending {p})" for t, tot, u, p, a in rows}
        send_email(emp["email"], f"Your leave balance summary - {date.today():%d %b %Y}",
                   email_html("Weekly leave balance", f"Hi {emp['name']}, here is where you stand for {year}.", details))
        sent += 1
    print(f"carry-forward run complete: {sent} employees processed")
    return {"employees": sent}
