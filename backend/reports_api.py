"""Manager / HR endpoints:  GET /calendar?month=YYYY-MM   GET /pending   GET /report?from=&to=
Managers see their own team; HRAdmin sees everyone."""
import csv
import io
from datetime import date, timedelta

from boto3.dynamodb.conditions import Attr, Key

from common import (ApiError, api, get_identity, parse_date, query_all, response, scan_all)


def _csv_safe(value):
    """Stop spreadsheet formula injection (a reason like '=HYPERLINK(...)')."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@api
def handler(event, context):
    emp, groups = get_identity(event)
    if not {"Manager", "HRAdmin"} & set(groups):
        raise ApiError(403, "Managers and HR only")
    route, qs = event["routeKey"], event.get("queryStringParameters") or {}
    is_hr = "HRAdmin" in groups

    employees = {e["employee_id"]: e for e in scan_all("EMPLOYEES_TABLE")}
    scope = {eid for eid, e in employees.items() if is_hr or e.get("manager_id") == emp["employee_id"]}

    if route == "GET /calendar":
        month = qs.get("month") or date.today().strftime("%Y-%m")
        first = parse_date(month + "-01")
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        items = query_all(
            "REQUESTS_TABLE", IndexName="status-start-index",
            KeyConditionExpression=Key("status").eq("APPROVED") & Key("start_date").lte(last.isoformat()),
            FilterExpression=Attr("end_date").gte(first.isoformat()))
        events = [{"employee_id": i["employee_id"], "name": employees.get(i["employee_id"], {}).get("name", i["employee_id"]),
                   "leave_type": i["leave_type"], "start_date": i["start_date"], "end_date": i["end_date"],
                   "days": i["days"]} for i in items if i["employee_id"] in scope]
        return response(200, {"month": month, "events": events})

    if route == "GET /pending":
        if is_hr:
            items = query_all("REQUESTS_TABLE", IndexName="status-start-index",
                              KeyConditionExpression=Key("status").eq("PENDING_HR"))
        else:
            items = query_all("REQUESTS_TABLE", IndexName="manager-status-index",
                              KeyConditionExpression=Key("manager_id").eq(emp["employee_id"])
                              & Key("status").eq("PENDING_MANAGER"))
        items.sort(key=lambda i: i["created_at"])
        return response(200, {"pending": [{k: i.get(k) for k in (
            "employee_id", "employee_name", "leave_type", "start_date", "end_date", "days", "reason",
            "created_at", "status")} for i in items]})

    # GET /report  -> CSV download of every request that touches the date range
    start = parse_date(qs.get("from") or date.today().replace(day=1).isoformat())
    end = parse_date(qs.get("to") or date.today().isoformat())
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["employee_id", "name", "leave_type", "start_date", "end_date", "days", "status",
                  "reason", "submitted_at", "decided_at"])
    for eid in sorted(scope):
        for i in query_all("REQUESTS_TABLE", KeyConditionExpression=Key("employee_id").eq(eid),
                           FilterExpression=Attr("start_date").lte(end.isoformat())
                           & Attr("end_date").gte(start.isoformat())):
            out.writerow([_csv_safe(x) for x in (
                eid, employees[eid]["name"], i["leave_type"], i["start_date"], i["end_date"], i["days"],
                i["status"], i.get("reason"), i.get("created_at"), i.get("decided_at"))])
    return {"statusCode": 200, "body": buf.getvalue(), "headers": {
        "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="leave-report.csv"'}}
