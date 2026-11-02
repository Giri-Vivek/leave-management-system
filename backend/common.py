"""Shared helpers used by every Lambda in the leave management system."""
import base64
import hashlib
import hmac
import html as _html
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache, wraps

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

_ddb = boto3.resource("dynamodb")
_ses = boto3.client("ses")
_secrets = boto3.client("secretsmanager")

# Statuses that still hold a balance reservation / block overlapping requests
ACTIVE = {"PENDING_MANAGER", "ESCALATED", "PENDING_HR"}
# Final statuses - a request never leaves these
TERMINAL = {"APPROVED", "REJECTED", "AUTO_REJECTED", "EXPIRED", "FAILED"}


# --------------------------------------------------------------------------- errors / responses
class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class InsufficientBalance(Exception):
    def __init__(self, available):
        super().__init__(f"available={available}")
        self.available = available


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=_Encoder),
    }


def api(fn):
    """Decorator: turn ApiError into a clean JSON error, log anything unexpected."""
    @wraps(fn)
    def wrapper(event, context):
        try:
            return fn(event, context)
        except ApiError as e:
            return response(e.status, {"error": e.message})
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return response(500, {"error": "Internal server error"})
    return wrapper


# --------------------------------------------------------------------------- DynamoDB access
@lru_cache(maxsize=None)
def table(env_name):
    return _ddb.Table(os.environ[env_name])


def scan_all(env_name, **kwargs):
    items, t = [], table(env_name)
    while True:
        r = t.scan(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def query_all(env_name, **kwargs):
    items, t = [], table(env_name)
    while True:
        r = t.query(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def get_config(leave_type):
    return table("CONFIG_TABLE").get_item(Key={"leave_type": leave_type}).get("Item")


def list_configs():
    return sorted(scan_all("CONFIG_TABLE"), key=lambda c: c["leave_type"])


def get_employee(employee_id):
    return table("EMPLOYEES_TABLE").get_item(Key={"employee_id": employee_id}).get("Item")


def get_request(employee_id, request_id):
    return table("REQUESTS_TABLE").get_item(
        Key={"employee_id": employee_id, "request_id": request_id}, ConsistentRead=True
    ).get("Item")


def get_identity(event):
    """Return (employee_item, cognito_groups) for the caller of an authorised API route."""
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    email = str(claims.get("email", "")).lower()
    raw = str(claims.get("cognito:groups", ""))          # HTTP API gives e.g. "[Manager HRAdmin]"
    groups = [g for g in raw.strip("[]").replace(",", " ").split() if g]
    items = table("EMPLOYEES_TABLE").query(
        IndexName="email-index", KeyConditionExpression=Key("email").eq(email)
    )["Items"]
    if not items:
        raise ApiError(403, "No employee record is linked to this account")
    return items[0], groups


# --------------------------------------------------------------------------- dates
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ApiError(400, "Dates must be in YYYY-MM-DD format")


def working_days(start, end):
    """Mon-Fri days between two dates, inclusive. (Extend here for public holidays.)"""
    days, d = 0, start
    while d <= end:
        if d.weekday() < 5:
            days += 1
        d += timedelta(days=1)
    return days


def new_request_id():
    """Time-sortable id so history queries come back in chronological order."""
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def find_overlap(employee_id, start_iso, end_iso):
    """Return an existing pending/approved request that intersects [start, end], else None."""
    items = query_all(
        "REQUESTS_TABLE",
        KeyConditionExpression=Key("employee_id").eq(employee_id),
        FilterExpression=Attr("status").is_in(sorted(ACTIVE | {"APPROVED"}))
        & Attr("start_date").lte(end_iso)
        & Attr("end_date").gte(start_iso),
    )
    return items[0] if items else None


# --------------------------------------------------------------------------- balances
def balance_key(employee_id, leave_type, year):
    return {"employee_id": employee_id, "leave_type_year": f"{leave_type}#{year}"}


def available(bal):
    return int(bal["total_quota"]) + int(bal["carried_forward"]) - int(bal["used"]) - int(bal["pending"])


def get_or_create_balance(employee_id, cfg, year):
    key = balance_key(employee_id, cfg["leave_type"], year)
    t = table("BALANCES_TABLE")
    item = t.get_item(Key=key, ConsistentRead=True).get("Item")
    if item:
        return item
    item = {**key, "leave_type": cfg["leave_type"], "year": year,
            "total_quota": int(cfg["annual_quota"]), "carried_forward": 0,
            "used": 0, "pending": 0, "version": 0}
    try:
        t.put_item(Item=item, ConditionExpression="attribute_not_exists(employee_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        item = t.get_item(Key=key, ConsistentRead=True)["Item"]
    return item


def _change_pending(employee_id, cfg, year, delta):
    """Optimistic-locking update of `pending` (retries if `version` moved underneath us)."""
    t = table("BALANCES_TABLE")
    for _ in range(5):
        bal = get_or_create_balance(employee_id, cfg, year)
        new_pending = int(bal["pending"]) + delta
        if delta > 0 and delta > available(bal):
            raise InsufficientBalance(available(bal))
        try:
            t.update_item(
                Key=balance_key(employee_id, cfg["leave_type"], year),
                UpdateExpression="SET pending = :p ADD version :one",
                ConditionExpression="version = :v",
                ExpressionAttributeValues={":p": max(0, new_pending),
                                           ":v": int(bal["version"]), ":one": 1},
            )
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    raise ApiError(409, "Your balance is being updated - please try again")


def reserve_days(employee_id, cfg, year, days):
    """Hold `days` as pending (NOT used). The `version` check stops two simultaneous
    submissions from both squeezing through the same last days."""
    _change_pending(employee_id, cfg, year, days)


def release_days(employee_id, cfg, year, days):
    _change_pending(employee_id, cfg, year, -days)


def finalize_request(employee_id, request_id, new_status, extra=None):
    """Move a request to a TERMINAL status and settle the balance in ONE transaction:
       APPROVED  -> used += days, pending -= days   (the only place `used` grows)
       otherwise -> pending -= days                 (reservation released)
    Idempotent: calling it again on a finished request is a no-op."""
    req = get_request(employee_id, request_id)
    if req["status"] in TERMINAL:
        return req
    cfg = get_config(req["leave_type"])
    days = int(req["days"])
    year = int(req["start_date"][:4])
    unlimited = bool(cfg.get("is_unlimited"))

    sets, names, values = ["#st = :new"], {"#st": "status"}, {":new": new_status}
    for i, (k, v) in enumerate((extra or {}).items()):
        if v is None:
            continue
        names[f"#k{i}"], values[f":v{i}"] = k, v
        sets.append(f"#k{i} = :v{i}")
    names["#ts"], values[":now"] = "decided_at", now_iso()
    sets.append("#ts = :now")
    active = sorted(ACTIVE)
    for i, s in enumerate(active):
        values[f":a{i}"] = s
    req_update = {"Update": {
        "TableName": table("REQUESTS_TABLE").name,
        "Key": {"employee_id": employee_id, "request_id": request_id},
        "UpdateExpression": "SET " + ", ".join(sets) + " REMOVE task_token, awaiting",
        "ConditionExpression": "#st IN (" + ", ".join(f":a{i}" for i in range(len(active))) + ")",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }}

    client = _ddb.meta.client
    for _ in range(5):
        items = [req_update]
        if not (unlimited and new_status != "APPROVED"):
            bal = get_or_create_balance(employee_id, cfg, year)
            used, pending = int(bal["used"]), int(bal["pending"])
            if not unlimited:
                pending = max(0, pending - days)
            if new_status == "APPROVED":
                used += days
                if not unlimited and int(bal["total_quota"]) + int(bal["carried_forward"]) - used - pending < 0:
                    raise InsufficientBalance(available(bal))
            items.append({"Update": {
                "TableName": table("BALANCES_TABLE").name,
                "Key": balance_key(employee_id, req["leave_type"], year),
                "UpdateExpression": "SET used = :u, pending = :p ADD version :one",
                "ConditionExpression": "version = :v",
                "ExpressionAttributeValues": {":u": used, ":p": pending,
                                              ":v": int(bal["version"]), ":one": 1},
            }})
        try:
            client.transact_write_items(TransactItems=items)
            return get_request(employee_id, request_id)
        except ClientError as e:
            if e.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            latest = get_request(employee_id, request_id)
            if latest["status"] in TERMINAL:      # someone else finished it first
                return latest
    raise ApiError(409, "Could not settle the balance - retry")


# --------------------------------------------------------------------------- signed approval links
@lru_cache(maxsize=1)
def _secret():
    return _secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"].encode()


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign_token(employee_id, request_id, stage, action, ttl_seconds):
    payload = "|".join([employee_id, request_id, stage, action, str(int(time.time()) + ttl_seconds)])
    body = _b64(payload.encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token):
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ApiError(403, "This link is not valid")
        employee_id, request_id, stage, action, exp = (
            base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split("|"))
        exp = int(exp)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "This link is malformed")
    if exp < time.time():
        raise ApiError(410, "This link has expired")
    return {"employee_id": employee_id, "request_id": request_id, "stage": stage, "action": action}


# --------------------------------------------------------------------------- email
def send_email(to, subject, html_body):
    """Never let an email failure break the workflow (SES sandbox rejects unverified recipients)."""
    if not to:
        return
    try:
        _ses.send_email(
            Source=os.environ["SENDER_EMAIL"],
            Destination={"ToAddresses": [to]},
            Message={"Subject": {"Data": subject},
                     "Body": {"Html": {"Data": html_body}, "Text": {"Data": subject}}},
        )
    except ClientError as e:
        print(f"SES send to {to} failed: {e}")


def email_html(heading, intro, details=None, buttons=None):
    e = _html.escape
    rows = "".join(
        f"<tr><td style='padding:4px 16px 4px 0;color:#667'>{e(str(k))}</td><td><b>{e(str(v))}</b></td></tr>"
        for k, v in (details or {}).items())
    btns = "".join(
        f"<a href='{e(url)}' style='display:inline-block;margin-right:10px;padding:10px 18px;"
        f"background:{color};color:#fff;text-decoration:none;border-radius:6px;font-weight:600'>{e(label)}</a>"
        for label, url, color in (buttons or []))
    return (f"<div style='font-family:Arial,sans-serif;max-width:520px;color:#14213D'>"
            f"<h2 style='margin:0 0 8px'>{e(heading)}</h2><p>{e(intro)}</p>"
            f"<table style='margin:12px 0'>{rows}</table><p>{btns}</p></div>")


def request_details(req):
    return {"Leave type": req["leave_type"], "From": req["start_date"], "To": req["end_date"],
            "Working days": int(req["days"]), "Reason": req.get("reason") or "-"}
