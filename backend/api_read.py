"""GET /me, GET /balances, GET /requests - read-only endpoints for the employee view."""
from datetime import date

from boto3.dynamodb.conditions import Key

from common import (api, available, get_identity, get_or_create_balance, list_configs, response,
                    table)


@api
def handler(event, context):
    route = event["routeKey"]
    emp, groups = get_identity(event)

    if route == "GET /me":
        return response(200, {"employee": {k: emp.get(k) for k in ("employee_id", "name", "email")},
                              "groups": groups})

    if route == "GET /balances":
        year, out = date.today().year, []
        for cfg in list_configs():
            bal = get_or_create_balance(emp["employee_id"], cfg, year)
            out.append({
                "leave_type": cfg["leave_type"], "description": cfg.get("description", ""),
                "unlimited": bool(cfg.get("is_unlimited")),
                "total": int(bal["total_quota"]) + int(bal["carried_forward"]),
                "carried_forward": int(bal["carried_forward"]), "used": int(bal["used"]),
                "pending": int(bal["pending"]), "available": available(bal),
            })
        return response(200, {"year": year, "balances": out})

    # GET /requests - newest first (request_id is time-sortable)
    items = table("REQUESTS_TABLE").query(
        KeyConditionExpression=Key("employee_id").eq(emp["employee_id"]),
        ScanIndexForward=False, Limit=100)["Items"]
    for i in items:
        i.pop("task_token", None)           # never expose the Step Functions token
    return response(200, {"requests": items})
