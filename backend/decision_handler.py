"""GET /decision?t=<signed token>  -> confirmation page
   POST /decision                 -> records the decision and resumes the Step Functions execution

Why two steps? Mail scanners (Outlook SafeLinks etc.) pre-fetch every link with GET. If GET
approved the request, a scanner could approve leave by accident. GET only shows a button."""
import base64
import html
import json
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError

from common import ApiError, get_request, table, verify_token

sfn = boto3.client("stepfunctions")
HEADERS = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
           "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}


def page(status, title, message, extra=""):
    e = html.escape
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<body style="font-family:system-ui,sans-serif;background:#F3F5F9;margin:0;display:grid;place-items:center;min-height:100vh">
<main style="background:#fff;max-width:440px;margin:16px;padding:28px;border-radius:10px;border:1px solid #D9DEE8;color:#14213D">
<h1 style="font-size:22px;margin:0 0 12px">{e(title)}</h1><p style="line-height:1.5">{e(message)}</p>{extra}</main></body></html>"""
    return {"statusCode": status, "headers": HEADERS, "body": body}


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    reason = ""
    if method == "POST":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        token, reason = form.get("t", ""), form.get("reason", "").strip()[:300]
    else:
        token = (event.get("queryStringParameters") or {}).get("t", "")

    try:
        claims = verify_token(token)
    except ApiError as e:
        return page(e.status, "Link problem", e.message)
    stage, action = claims["stage"], claims["action"]
    req = get_request(claims["employee_id"], claims["request_id"])
    if not req:
        return page(404, "Not found", "This leave request no longer exists.")
    if req.get("awaiting") != stage or "task_token" not in req:
        return page(409, "Already handled",
                    f"This request is no longer waiting for your decision (status: {req['status']}).")

    if method == "GET":
        e = html.escape
        summary = (f"<p><b>{e(req['employee_name'])}</b> - {e(req['leave_type'])}<br>"
                   f"{e(req['start_date'])} to {e(req['end_date'])} ({int(req['days'])} working days)<br>"
                   f"<i>{e(req.get('reason') or 'No reason given')}</i></p>")
        approve = action == "APPROVE"
        field = "" if approve else ('<label>Reason (optional)<br><textarea name="reason" rows="3" '
                                    'style="width:100%;box-sizing:border-box"></textarea></label><br><br>')
        form_html = (f'{summary}<form method="POST" action="/decision">'
                     f'<input type="hidden" name="t" value="{e(token)}">{field}'
                     f'<button style="padding:10px 20px;border:0;border-radius:6px;color:#fff;font-weight:600;'
                     f'background:{"#15803D" if approve else "#B91C1C"}">'
                     f'{"Approve leave" if approve else "Reject leave"}</button></form>')
        return page(200, "Confirm your decision", "Please confirm below.", form_html)

    # ---- POST: single-use. Atomically remove the task token; only one click can win. -----
    decision = "APPROVED" if action == "APPROVE" else "REJECTED"
    try:
        old = table("REQUESTS_TABLE").update_item(
            Key={"employee_id": req["employee_id"], "request_id": req["request_id"]},
            UpdateExpression="REMOVE task_token",
            ConditionExpression="attribute_exists(task_token) AND awaiting = :s",
            ExpressionAttributeValues={":s": stage}, ReturnValues="ALL_OLD")["Attributes"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return page(409, "Already handled", "Someone already used this link.")
        raise
    try:
        sfn.send_task_success(taskToken=old["task_token"], output=json.dumps({
            "decision": decision, "stage": stage, "reason": reason,
            "decided_by": req["manager_id"] if stage == "MANAGER" else "HR_ADMIN"}))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist"):
            return page(410, "Too late", "The approval window closed before your decision arrived.")
        raise
    return page(200, "Decision recorded", f"The request was {decision.lower()}. The employee has been notified.")
