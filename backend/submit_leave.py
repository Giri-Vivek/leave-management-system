"""POST /leave - validate, enforce quota + conflicts, then start the approval workflow."""
import json
import os
from datetime import date

import boto3

from common import (ApiError, InsufficientBalance, api, email_html, find_overlap, finalize_request,
                    get_config, get_identity, new_request_id, now_iso, parse_date, release_days,
                    request_details, reserve_days, response, send_email, table, working_days)

sfn = boto3.client("stepfunctions")


@api
def handler(event, context):
    emp, _groups = get_identity(event)
    body = json.loads(event.get("body") or "{}")
    leave_type = str(body.get("leave_type", "")).upper()
    start, end = parse_date(body.get("start_date")), parse_date(body.get("end_date"))
    reason = str(body.get("reason", "")).strip()[:500]

    # ---- 1. basic validation ---------------------------------------------------------------
    cfg = get_config(leave_type)
    if not cfg:
        raise ApiError(400, f"Unknown leave type '{leave_type}'")
    if end < start:
        raise ApiError(400, "End date is before the start date")
    if start.year != end.year:
        raise ApiError(400, "Request spans two years - submit one request per year")
    if start < date.today() and leave_type != "SICK":
        raise ApiError(400, "Only sick leave can be back-dated")
    days = working_days(start, end)
    if days == 0:
        raise ApiError(400, "The selected dates only contain weekends")
    if not emp.get("manager_id") or emp["manager_id"] == emp["employee_id"]:
        raise ApiError(400, "No approving manager is set for your account - contact HR")

    # ---- 2. conflict detection -------------------------------------------------------------
    clash = find_overlap(emp["employee_id"], start.isoformat(), end.isoformat())
    if clash:
        raise ApiError(409, f"Overlaps your {clash['status'].lower().replace('_', ' ')} request "
                            f"({clash['start_date']} to {clash['end_date']})")

    request_id = new_request_id()
    item = {
        "employee_id": emp["employee_id"], "request_id": request_id, "employee_name": emp["name"],
        "leave_type": leave_type, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "days": days, "reason": reason, "status": "PENDING_MANAGER",
        "manager_id": emp["manager_id"], "created_at": now_iso(),
        "requires_hr": days > int(cfg.get("requires_hr_above_days", 5)),
    }
    unlimited = bool(cfg.get("is_unlimited"))

    # ---- 3. quota enforcement (reserve as `pending`; `used` only changes on final approval) -
    reserved = False
    if not unlimited:
        try:
            reserve_days(emp["employee_id"], cfg, start.year, days)
            reserved = True
        except InsufficientBalance as e:
            item.update(status="AUTO_REJECTED", decided_at=now_iso(),
                        reject_reason=f"Insufficient {leave_type} balance: requested {days} day(s), "
                                      f"only {e.available} available")
            table("REQUESTS_TABLE").put_item(Item=item)
            send_email(emp["email"], f"Leave request auto-rejected ({leave_type})",
                       email_html("Request auto-rejected", item["reject_reason"], request_details(item)))
            return response(201, {"request": item, "message": item["reject_reason"]})

    # ---- 4. persist (give the days back if the save fails) ---------------------------------
    try:
        table("REQUESTS_TABLE").put_item(Item=item)
    except Exception:
        if reserved:
            release_days(emp["employee_id"], cfg, start.year, days)
        raise

    # ---- 5. start the Step Functions approval workflow -------------------------------------
    arn = os.environ.get("STATE_MACHINE_ARN", "")
    if os.environ.get("ENABLE_WORKFLOW") == "true" and arn:
        try:
            ex = sfn.start_execution(
                stateMachineArn=arn, name=request_id,
                input=json.dumps({
                    "employee_id": emp["employee_id"], "request_id": request_id, "days": days,
                    "requires_hr": item["requires_hr"],
                    "approval_timeout": int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "172800")),
                }))
            table("REQUESTS_TABLE").update_item(
                Key={"employee_id": emp["employee_id"], "request_id": request_id},
                UpdateExpression="SET execution_arn = :a",
                ExpressionAttributeValues={":a": ex["executionArn"]})
            item["execution_arn"] = ex["executionArn"]
        except Exception:
            finalize_request(emp["employee_id"], request_id, "FAILED",
                             {"reject_reason": "Could not start the approval workflow"})
            raise

    send_email(emp["email"], f"Leave request submitted ({leave_type}, {days} day(s))",
               email_html("Request submitted",
                          "Your manager has been notified. You will get an email at every stage.",
                          request_details(item)))
    return response(201, {"request": item, "message": "Request submitted - waiting for manager approval"})
