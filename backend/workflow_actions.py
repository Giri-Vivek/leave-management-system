"""Called by Step Functions for every non-waiting step: REMIND, FINAL_APPROVE, FINAL_REJECT,
EXPIRE, FAIL. Each action is idempotent so Step Functions can safely retry it.
(ESCALATE is legacy: it only exists so executions started before reminders replaced
escalation can still finish. New requests never use it.)"""
import os

from common import (available, email_html, finalize_request, get_config, get_employee,
                    get_or_create_balance, now_iso, request_details, send_email, table)

# How many reminder emails an approver gets (one per approval_timeout) before the request expires
MAX_REMINDERS = int(os.environ.get("MAX_REMINDERS", "7"))


def _remaining_line(req):
    cfg = get_config(req["leave_type"])
    if not cfg or cfg.get("is_unlimited"):
        return {}
    bal = get_or_create_balance(req["employee_id"], cfg, int(req["start_date"][:4]))
    return {"Balance left": f"{available(bal)} day(s)"}


def handler(event, context):
    action, r = event["action"], event["request"]
    eid, rid = r["employee_id"], r["request_id"]
    emp = get_employee(eid)

    if action == "REMIND":
        # The reminder EMAIL is sent by notify_approver when the workflow loops back to the
        # waiting step (fresh approve/reject links). Here we only count, per approver stage.
        stage = event.get("stage", "MANAGER")
        prev = r.get("reminder") or {}
        count = (int(prev.get("count", 0)) if prev.get("stage") == stage else 0) + 1
        return {"stage": stage, "count": count, "give_up": count > MAX_REMINDERS}

    if action == "ESCALATE":    # legacy, see module docstring
        table("REQUESTS_TABLE").update_item(
            Key={"employee_id": eid, "request_id": rid},
            UpdateExpression="SET #st = :e, escalated_at = :n REMOVE task_token, awaiting",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":e": "ESCALATED", ":n": now_iso()})
        send_email(emp["email"], "Your leave request was escalated to HR", email_html(
            "Escalated to HR", "Your manager did not respond within the approval window, so HR will decide.", {}))
        return {"status": "ESCALATED"}

    if action == "FINAL_APPROVE":
        req = finalize_request(eid, rid, "APPROVED")
        who = (r.get("hr_result") or r.get("manager_result") or {}).get("decided_by")
        table("REQUESTS_TABLE").update_item(
            Key={"employee_id": eid, "request_id": rid},
            UpdateExpression="SET approved_by = :w", ExpressionAttributeValues={":w": who or "-"})
        send_email(emp["email"], f"Leave approved ({req['leave_type']})", email_html(
            "Leave approved", "Your leave request has been approved.",
            {**request_details(req), **_remaining_line(req)}))
        return {"status": "APPROVED"}

    if action == "FINAL_REJECT":
        last = r.get("hr_result") or r.get("manager_result") or {}
        req = finalize_request(eid, rid, "REJECTED", {"reject_reason": last.get("reason") or "No reason given",
                                                       "rejected_by": last.get("decided_by")})
        send_email(emp["email"], f"Leave rejected ({req['leave_type']})", email_html(
            "Leave rejected", last.get("reason") or "Your leave request was not approved.",
            request_details(req)))
        return {"status": "REJECTED"}

    if action == "EXPIRE":
        req = finalize_request(eid, rid, "EXPIRED", {"reject_reason": "No decision after several reminders"})
        send_email(emp["email"], f"Leave request expired ({req['leave_type']})", email_html(
            "Request expired", "Nobody decided after several reminders. Your days were released - please resubmit.",
            request_details(req)))
        return {"status": "EXPIRED"}

    if action == "FAIL":
        finalize_request(eid, rid, "FAILED", {"reject_reason": "Workflow error - contact HR"})
        send_email(emp["email"], "Leave request could not be processed", email_html(
            "Processing error", "Something went wrong. Your days were released - please resubmit or contact HR.", {}))
        return {"status": "FAILED"}

    raise ValueError(f"Unknown workflow action: {action}")
