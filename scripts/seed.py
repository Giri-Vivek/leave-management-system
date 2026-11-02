#!/usr/bin/env python3
"""Load leave config + employees into DynamoDB and create / update their Cognito logins.

  py scripts\\seed.py --email-base you@gmail.com --verify-ses

Employees come from schema/seed/employees.local.json if that file exists (your own copy,
git-ignored), otherwise schema/seed/employees.json. Override with --employees <path>.

Each employee: employee_id, name, email, manager_id, role (Employee | Manager | HRAdmin),
and an optional "password" (otherwise --password is used).

--email-base only rewrites placeholder emails ending in @example.com, to you+emp001@gmail.com
style addresses. Real emails are kept exactly as written. Safe to re-run: it updates names,
roles and passwords, and never duplicates anyone.
"""
import argparse
import json
import pathlib
import re
import subprocess
from decimal import Decimal

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROLES = {"Employee", "Manager", "HRAdmin"}


def tf_outputs():
    raw = subprocess.check_output(["terraform", "output", "-json"], cwd=ROOT / "infra")
    return {k: v["value"] for k, v in json.loads(raw).items()}


def check_password(who, pw):
    if len(pw) < 8 or not re.search(r"[a-z]", pw) or not re.search(r"[A-Z]", pw) or not re.search(r"\d", pw):
        raise SystemExit(f"Password for {who} is too weak: use 8+ characters with upper case, lower case and a number.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="leave-mgmt")
    ap.add_argument("--employees", help="path to an employees JSON file")
    ap.add_argument("--email-base", help="e.g. you@gmail.com (plus-addressing for @example.com placeholders)")
    ap.add_argument("--password", default="Leave@12345", help="password for users that have no own password")
    ap.add_argument("--verify-ses", action="store_true", help="send SES verification emails")
    args = ap.parse_args()

    local = ROOT / "schema/seed/employees.local.json"
    path = pathlib.Path(args.employees) if args.employees else (local if local.exists() else ROOT / "schema/seed/employees.json")
    print(f"Using employees file: {path}")
    check_password("--password", args.password)

    out = tf_outputs()
    region = out["region"]
    ddb = boto3.resource("dynamodb", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)
    ses = boto3.client("ses", region_name=region)
    load = lambda p: json.loads(pathlib.Path(p).read_text(encoding="utf-8"), parse_float=Decimal)

    cfg_table = ddb.Table(f"{args.project}-leave-config")
    for item in load(ROOT / "schema/seed/leave_config.json"):
        cfg_table.put_item(Item=item)
    print("leave_config: ok")

    emp_table = ddb.Table(f"{args.project}-employees")
    hr_emails = []
    for emp in load(path):
        if emp.get("role") not in ROLES:
            raise SystemExit(f"{emp.get('employee_id')}: role must be one of {sorted(ROLES)}")
        pw = emp.pop("password", None) or args.password      # never stored in DynamoDB
        check_password(emp["employee_id"], pw)
        if args.email_base and emp["email"].lower().endswith("@example.com"):
            local_part, domain = args.email_base.split("@")
            emp["email"] = f"{local_part}+{emp['employee_id'].lower()}@{domain}"
        emp["email"] = emp["email"].strip().lower()
        emp_table.put_item(Item=emp)

        try:
            cognito.admin_create_user(
                UserPoolId=out["user_pool_id"], Username=emp["email"], MessageAction="SUPPRESS",
                UserAttributes=[{"Name": "email", "Value": emp["email"]},
                                {"Name": "email_verified", "Value": "true"}])
        except cognito.exceptions.UsernameExistsException:
            pass
        cognito.admin_set_user_password(UserPoolId=out["user_pool_id"], Username=emp["email"], Password=pw, Permanent=True)
        # keep group membership in sync with the role (remove old groups first)
        for g in cognito.admin_list_groups_for_user(UserPoolId=out["user_pool_id"], Username=emp["email"])["Groups"]:
            if g["GroupName"] != emp["role"]:
                cognito.admin_remove_user_from_group(UserPoolId=out["user_pool_id"], Username=emp["email"], GroupName=g["GroupName"])
        cognito.admin_add_user_to_group(UserPoolId=out["user_pool_id"], Username=emp["email"], GroupName=emp["role"])
        if args.verify_ses:
            ses.verify_email_identity(EmailAddress=emp["email"])
        if emp["role"] == "HRAdmin":
            hr_emails.append(emp["email"])
        print(f"employee + login: {emp['employee_id']:7} {emp['email']:38} {emp['name']:20} group={emp['role']}")

    if args.verify_ses:
        print("SES: verification emails sent - click each link in your inbox before testing")
    for e in hr_emails:
        print(f"\nREMINDER: HR admin is {e}. Terraform's hr_email must be exactly this address "
              f"(re-run terraform apply with -var hr_email=... if you changed it).")
    print(f"Users without their own \"password\" use: {args.password}")


if __name__ == "__main__":
    main()
