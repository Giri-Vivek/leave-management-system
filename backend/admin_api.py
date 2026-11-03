"""HR-only endpoints (the API checks the HRAdmin group, not just the UI):
  GET  /admin/balances   every employee's balance per leave type for this year
  POST /admin/balance    {employee_id, leave_type, delta, note}  add or remove days for one person
  GET  /admin/config     leave rules (annual quota, carry-forward, HR-approval threshold)
  POST /admin/config     edit one leave type's rules; optionally apply a quota change to existing balances
"""
import json
from datetime import date

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from common import (ApiError, api, available, balance_key, email_html, get_config, get_employee,
                    get_identity, get_or_create_balance, list_configs, now_iso, response, scan_all,
                    send_email, table)


def _int(body, key, lo, hi):
    try:
        v = int(body.get(key))
    except (TypeError, ValueError):
        raise ApiError(400, f"{key} must be a whole number")
    if not lo <= v <= hi:
        raise ApiError(400, f"{key} must be between {lo} and {hi}")
    return v


def _public_cfg(c):
    return {"leave_type": c["leave_type"], "description": c.get("description", ""),
            "annual_quota": int(c["annual_quota"]), "is_unlimited": bool(c.get("is_unlimited")),
            "carry_forward_allowed": bool(c.get("carry_forward_allowed")),
            "max_carry_forward": int(c.get("max_carry_forward", 0)),
            "requires_hr_above_days": int(c.get("requires_hr_above_days", 5)),
            "updated_by": c.get("updated_by"), "updated_at": c.get("updated_at")}


def adjust_total(employee_id, cfg, year, delta, by, note, clamp=False):
    """Change one person's entitlement for the year by `delta` days.
    Optimistic lock on `version`, so it is safe next to submissions and approvals.
    It never lets available drop below 0 (days already used or pending are protected):
    clamp=False raises an error, clamp=True stops at the lowest safe value instead."""
    t = table("BALANCES_TABLE")
    entry = {"by": by, "at": now_iso(), "delta": delta, "note": (note or "Adjusted by HR")[:200]}
    for _ in range(5):
        bal = get_or_create_balance(employee_id, cfg, year)
        lowest = max(int(bal["used"]) + int(bal["pending"]) - int(bal["carried_forward"]), 0)
        new_total = int(bal["total_quota"]) + delta
        if new_total < lowest:
            if not clamp:
                raise ApiError(409, f"Cannot go lower: {int(bal['used'])} day(s) used and "
                                    f"{int(bal['pending'])} pending ({available(bal)} available now)")
            new_total = lowest
        if new_total == int(bal["total_quota"]):
            return new_total
        try:
            t.update_item(
                Key=balance_key(employee_id, cfg["leave_type"], year),
                UpdateExpression="SET total_quota = :t, adjustments = list_append(if_not_exists(adjustments, :empty), :e) "
                                 "ADD version :one",
                ConditionExpression="version = :v",
                ExpressionAttributeValues={":t": new_total, ":v": int(bal["version"]), ":one": 1,
                                           ":empty": [], ":e": [entry]})
            return new_total
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    raise ApiError(409, "That balance is being updated - please try again")


def apply_quota_change(leave_type, year, delta, by):
    cfg = get_config(leave_type)
    rows = scan_all("BALANCES_TABLE", FilterExpression=Attr("leave_type").eq(leave_type) & Attr("year").eq(year))
    updated = clamped = 0
    for r in rows:
        before = int(r["total_quota"])
        after = adjust_total(r["employee_id"], cfg, year, delta, by,
                             f"Annual quota changed by HR ({delta:+d})", clamp=True)
        if after != before:
            updated += 1
        if after != before + delta:
            clamped += 1
    return {"updated_balances": updated, "clamped": clamped}


def balance_view(bal):
    total = int(bal["total_quota"]) + int(bal["carried_forward"])
    return {"total": total, "used": int(bal["used"]), "pending": int(bal["pending"]), "available": available(bal)}


@api
def handler(event, context):
    route = event["routeKey"]
    me, groups = get_identity(event)
    if "HRAdmin" not in groups:
        raise ApiError(403, "HR admins only")
    year = date.today().year
    body = json.loads(event.get("body") or "{}") if event.get("body") else {}

    # ------------------------------------------------------------------ leave rules
    if route == "GET /admin/config":
        return response(200, {"config": [_public_cfg(c) for c in list_configs()]})

    if route == "POST /admin/config":
        leave_type = str(body.get("leave_type", "")).upper()
        cfg = get_config(leave_type)
        if not cfg:
            raise ApiError(404, f"Unknown leave type '{leave_type}'")
        unlimited = bool(cfg.get("is_unlimited"))
        quota = int(cfg["annual_quota"]) if unlimited else _int(body, "annual_quota", 0, 365)
        cf_allowed = bool(body.get("carry_forward_allowed"))
        max_cf = _int(body, "max_carry_forward", 0, 365) if cf_allowed else 0
        hr_above = _int(body, "requires_hr_above_days", 1, 365)
        desc = str(body.get("description", cfg.get("description", ""))).strip()[:100]
        delta = quota - int(cfg["annual_quota"])
        table("CONFIG_TABLE").update_item(
            Key={"leave_type": leave_type},
            UpdateExpression="SET #q = :q, #c = :c, #m = :m, #h = :h, #d = :d, #ub = :ub, #ua = :ua",
            ConditionExpression="attribute_exists(leave_type)",
            ExpressionAttributeNames={"#q": "annual_quota", "#c": "carry_forward_allowed", "#m": "max_carry_forward",
                                      "#h": "requires_hr_above_days", "#d": "description",
                                      "#ub": "updated_by", "#ua": "updated_at"},
            ExpressionAttributeValues={":q": quota, ":c": cf_allowed, ":m": max_cf, ":h": hr_above, ":d": desc,
                                       ":ub": me["employee_id"], ":ua": now_iso()})
        result = {"updated_balances": 0, "clamped": 0}
        if delta and not unlimited and body.get("apply_to_existing", True):
            result = apply_quota_change(leave_type, year, delta, me["employee_id"])
        return response(200, {"config": _public_cfg(get_config(leave_type)), **result})

    # ------------------------------------------------------------------ balances
    if route == "GET /admin/balances":
        configs = [c for c in list_configs() if not c.get("is_unlimited")]
        rows = {(b["employee_id"], b["leave_type"]): b
                for b in scan_all("BALANCES_TABLE", FilterExpression=Attr("year").eq(year))}
        out = []
        for e in sorted(scan_all("EMPLOYEES_TABLE"), key=lambda x: x["name"].lower()):
            cells = {}
            for c in configs:
                b = rows.get((e["employee_id"], c["leave_type"]))
                if b:
                    cells[c["leave_type"]] = balance_view(b)
                else:   # no row yet: show the default, it is created on the first adjustment
                    q = int(c["annual_quota"])
                    cells[c["leave_type"]] = {"total": q, "used": 0, "pending": 0, "available": q}
            out.append({"employee_id": e["employee_id"], "name": e["name"], "role": e.get("role", ""), "balances": cells})
        return response(200, {"year": year, "leave_types": [c["leave_type"] for c in configs], "employees": out})

    if route == "POST /admin/balance":
        employee = get_employee(str(body.get("employee_id", "")))
        if not employee:
            raise ApiError(404, "Unknown employee")
        leave_type = str(body.get("leave_type", "")).upper()
        cfg = get_config(leave_type)
        if not cfg:
            raise ApiError(404, f"Unknown leave type '{leave_type}'")
        if cfg.get("is_unlimited"):
            raise ApiError(400, f"{leave_type} has no quota to adjust")
        delta = _int(body, "delta", -365, 365)
        if delta == 0:
            raise ApiError(400, "delta cannot be 0")
        adjust_total(employee["employee_id"], cfg, year, delta, me["employee_id"], str(body.get("note", "")))
        bal = get_or_create_balance(employee["employee_id"], cfg, year)
        send_email(employee["email"], f"Your {leave_type} leave balance was updated", email_html(
            "Leave balance updated by HR", f"HR changed your {leave_type} leave by {delta:+d} day(s).",
            {"Available now": f"{available(bal)} day(s)"}))
        return response(200, {"employee_id": employee["employee_id"], "leave_type": leave_type,
                              "balance": balance_view(bal)})

    raise ApiError(404, "Unknown route")
