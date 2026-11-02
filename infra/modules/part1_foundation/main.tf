data "aws_caller_identity" "me" {}

locals {
  # Built from the name (not referenced) so Part 1 never depends on Part 2
  state_machine_arn = "arn:aws:states:${var.region}:${data.aws_caller_identity.me.account_id}:stateMachine:${var.project}-approval"
}

# ------------------------------------------------------------------ DynamoDB
resource "aws_dynamodb_table" "config" {
  name         = "${var.project}-leave-config"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "leave_type"
  attribute {
    name = "leave_type"
    type = "S"
  }
}

resource "aws_dynamodb_table" "employees" {
  name         = "${var.project}-employees"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "employee_id"
  attribute {
    name = "employee_id"
    type = "S"
  }
  attribute {
    name = "email"
    type = "S"
  }
  global_secondary_index {
    name            = "email-index"
    hash_key        = "email"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "balances" {
  name         = "${var.project}-leave-balances"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "employee_id"
  range_key    = "leave_type_year"
  attribute {
    name = "employee_id"
    type = "S"
  }
  attribute {
    name = "leave_type_year"
    type = "S"
  }
}

resource "aws_dynamodb_table" "requests" {
  name         = "${var.project}-leave-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "employee_id"
  range_key    = "request_id"
  attribute {
    name = "employee_id"
    type = "S"
  }
  attribute {
    name = "request_id"
    type = "S"
  }
  attribute {
    name = "manager_id"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }
  attribute {
    name = "start_date"
    type = "S"
  }
  global_secondary_index {
    name            = "manager-status-index"
    hash_key        = "manager_id"
    range_key       = "status"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "status-start-index"
    hash_key        = "status"
    range_key       = "start_date"
    projection_type = "ALL"
  }
  point_in_time_recovery {
    enabled = true
  }
}

# ------------------------------------------------------------------ Cognito
resource "aws_cognito_user_pool" "pool" {
  name                     = "${var.project}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                          = "web"
  user_pool_id                  = aws_cognito_user_pool.pool.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_group" "groups" {
  for_each     = toset(["Employee", "Manager", "HRAdmin"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.pool.id
}

# ------------------------------------------------------------------ SES
resource "aws_ses_email_identity" "sender" {
  email = var.sender_email
}

# ------------------------------------------------------------------ Lambda package + role
data "archive_file" "backend" {
  type        = "zip"
  source_dir  = "${path.root}/../backend"
  output_path = "${path.root}/build/backend.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

locals {
  dynamodb_arns = flatten([for t in [
    aws_dynamodb_table.config, aws_dynamodb_table.employees,
    aws_dynamodb_table.balances, aws_dynamodb_table.requests
  ] : [t.arn, "${t.arn}/index/*"]])

  common_env = {
    CONFIG_TABLE             = aws_dynamodb_table.config.name
    EMPLOYEES_TABLE          = aws_dynamodb_table.employees.name
    BALANCES_TABLE           = aws_dynamodb_table.balances.name
    REQUESTS_TABLE           = aws_dynamodb_table.requests.name
    SENDER_EMAIL             = var.sender_email
    STATE_MACHINE_ARN        = local.state_machine_arn
    ENABLE_WORKFLOW          = tostring(var.enable_workflow)
    APPROVAL_TIMEOUT_SECONDS = tostring(var.approval_timeout_seconds)
  }
}

resource "aws_iam_role" "lambda" {
  name = "${var.project}-part1-lambda"
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
        Resource = local.dynamodb_arns
      },
      { Effect = "Allow", Action = ["ses:SendEmail"], Resource = "*" },
      { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.state_machine_arn }
    ]
  })
}

resource "aws_lambda_function" "fn" {
  for_each         = toset(["submit_leave", "api_read"])
  function_name    = "${var.project}-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "${each.key}.handler"
  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256
  timeout          = 15
  memory_size      = 256
  environment {
    variables = local.common_env
  }
}

# ------------------------------------------------------------------ HTTP API
resource "aws_apigatewayv2_api" "api" {
  name          = "${var.project}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["*"] # tighten to the CloudFront URL once deployed
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.api.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://${aws_cognito_user_pool.pool.endpoint}"
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each               = aws_lambda_function.fn
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = each.value.invoke_arn
  payload_format_version = "2.0"
}

locals {
  routes = {
    "POST /leave"   = "submit_leave"
    "GET /me"       = "api_read"
    "GET /balances" = "api_read"
    "GET /requests" = "api_read"
  }
}

resource "aws_apigatewayv2_route" "r" {
  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.fn[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "api" {
  for_each      = aws_lambda_function.fn
  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = each.value.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
