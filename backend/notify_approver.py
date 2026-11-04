"""Called by Step Functions (waitForTaskToken). Emails the approver signed links, then the
workflow PAUSES until the approver clicks one. If the timeout passes, the workflow comes back
here with request['reminder'] set, so the same approver gets a REMINDER with fresh links."""
import os
from datetime import datetime, timezone

import boto3

from common import (email_html, get_employee, get_request, now_iso, request_details, send_email,
                    sign_token, table)

sns = boto3.client("sns")


def _waiting_text(created_at):
    try:
        hours = int((datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).total_seconds() // 3600)
    except (TypeError, ValueError):
        return "a while"
    return f"{hours} hour(s)" if hours < 48 else f"{hours // 24} day(s)"


def handler(event, context):
    stage, token, r = event["stage"], event["task_token"], event["request"]
    eid, rid = r["employee_id"], r["request_id"]
    req, employee = get_request(eid, rid), get_employee(eid)

    if stage == "MANAGER":
        approver, status = get_employee(req["manager_id"]), "PENDING_MANAGER"
    else:
        approver = {"employee_id": "HR_ADMIN", "name": "HR Admin", "email": os.environ["HR_EMAIL"]}
        status = "PENDING_HR"

    # Park the (new) task token on the request. Only a signed link can release it.
    table("REQUESTS_TABLE").update_item(
        Key={"employee_id": eid, "request_id": rid},
        UpdateExpression="SET task_token = :t, awaiting = :a, #st = :s, notified_at = :n",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":t": token, ":a": stage, ":s": status, ":n": now_iso()})

    ttl = int(r.get("approval_timeout", 86400)) + 3600
    base = os.environ["API_BASE_URL"].rstrip("/")
    link = {a: f"{base}/decision?t={sign_token(eid, rid, stage, a, ttl)}" for a in ("APPROVE", "REJECT")}

    reminder = r.get("reminder") or {}
    n = int(reminder.get("count", 0)) if reminder.get("stage") == stage else 0
    base_subject = f"Action needed: {employee['name']}, {req['leave_type']} leave, {int(req['days'])} day(s)"
    if n:
        subject = f"[Reminder {n}] {base_subject}"
        intro = (f"This leave request from {employee['name']} has been waiting for your decision for "
                 f"{_waiting_text(req.get('created_at'))}. Please review it.")
    else:
        subject = f"[Action needed] {employee['name']}: {req['leave_type']} leave, {int(req['days'])} day(s)"
        intro = f"{employee['name']} has requested leave and needs your decision."

    send_email(approver["email"], subject, email_html(
        "Leave request still pending" if n else "Leave approval needed", intro, request_details(req),
        [("Review and approve", link["APPROVE"], "#15803D"), ("Review and reject", link["REJECT"], "#B91C1C")]))

    # SNS notification (no links in it - topic subscribers must not receive approval powers)
    try:
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"], Subject=subject[:100],
            Message=f"{employee['name']} requested {int(req['days'])} day(s) of {req['leave_type']} leave "
                    f"({req['start_date']} to {req['end_date']}). Approval links were emailed to the approver.",
            MessageAttributes={"approver": {"DataType": "String", "StringValue": approver["employee_id"]}})
    except Exception as e:  # noqa: BLE001
        print(f"SNS publish failed: {e}")
