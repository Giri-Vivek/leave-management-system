# ------------------------------------------------------------------ link-signing secret + SNS
resource "random_password" "token_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "token" {
  name                    = "${var.project}-link-signing-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "token" {
  secret_id     = aws_secretsmanager_secret.token.id
  secret_string = random_password.token_secret.result
}

resource "aws_sns_topic" "manager" {
  name = "${var.project}-manager-notifications"
}

# ------------------------------------------------------------------ Lambdas
resource "aws_iam_role" "lambda" {
  name = "${var.project}-part2-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  name = "access"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query",
        "dynamodb:Scan", "dynamodb:ConditionCheckItem"]
        Resource = var.dynamodb_arns
      },
      { Effect = "Allow", Action = ["ses:SendEmail"], Resource = "*" },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.manager.arn },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.token.arn },
      # Task-token callbacks do not support resource-level permissions
      { Effect = "Allow", Action = ["states:SendTaskSuccess", "states:SendTaskFailure"], Resource = "*" }
    ]
  })
}

locals {
  env = merge(var.common_env, {
    SECRET_ARN    = aws_secretsmanager_secret.token.arn
    SNS_TOPIC_ARN = aws_sns_topic.manager.arn
    HR_EMAIL      = var.hr_email
    API_BASE_URL  = var.api_endpoint
  })
}

resource "aws_lambda_function" "fn" {
  for_each         = toset(["notify_approver", "decision_handler", "workflow_actions", "carry_forward"])
  function_name    = "${var.project}-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "${each.key}.handler"
  filename         = var.zip_path
  source_code_hash = var.zip_hash
  timeout          = each.key == "carry_forward" ? 300 : 20
  memory_size      = 256
  environment {
    variables = local.env
  }
}

# ------------------------------------------------------------------ Step Functions
resource "aws_iam_role" "sfn" {
  name = "${var.project}-sfn"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "invoke-lambdas"
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      Resource = [
        aws_lambda_function.fn["notify_approver"].arn, "${aws_lambda_function.fn["notify_approver"].arn}:*",
        aws_lambda_function.fn["workflow_actions"].arn, "${aws_lambda_function.fn["workflow_actions"].arn}:*"
      ]
    }]
  })
}

resource "aws_sfn_state_machine" "approval" {
  name     = "${var.project}-approval" # Part 1 builds this ARN from the same name
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"
  definition = templatefile("${path.root}/../statemachine/leave_approval.asl.json", {
    NOTIFY_APPROVER_ARN  = aws_lambda_function.fn["notify_approver"].arn
    WORKFLOW_ACTIONS_ARN = aws_lambda_function.fn["workflow_actions"].arn
  })
}

# ------------------------------------------------------------------ public /decision route (signed link)
resource "aws_apigatewayv2_integration" "decision" {
  api_id                 = var.api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["decision_handler"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "decision" {
  for_each  = toset(["GET /decision", "POST /decision"])
  api_id    = var.api_id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.decision.id}"
  # No JWT authorizer: the HMAC-signed token in the URL IS the authentication.
}

resource "aws_lambda_permission" "decision" {
  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["decision_handler"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_execution_arn}/*/*"
}

# ------------------------------------------------------------------ weekly EventBridge schedule
resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${var.project}-weekly-balance"
  schedule_expression = "cron(30 3 ? * MON *)" # Mondays 03:30 UTC = 09:00 IST
}

resource "aws_cloudwatch_event_target" "weekly" {
  rule = aws_cloudwatch_event_rule.weekly.name
  arn  = aws_lambda_function.fn["carry_forward"].arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["carry_forward"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly.arn
}
