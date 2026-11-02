data "aws_caller_identity" "me" {}

# ------------------------------------------------------------------ reports Lambda + routes
resource "aws_iam_role" "lambda" {
  name = "${var.project}-part3-lambda"
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
  name = "read-only"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
      Resource = var.dynamodb_arns
    }]
  })
}

resource "aws_lambda_function" "reports" {
  function_name    = "${var.project}-reports_api"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "reports_api.handler"
  filename         = var.zip_path
  source_code_hash = var.zip_hash
  timeout          = 20
  memory_size      = 256
  environment {
    variables = var.common_env
  }
}

resource "aws_apigatewayv2_integration" "reports" {
  api_id                 = var.api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.reports.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "reports" {
  for_each           = toset(["GET /calendar", "GET /pending", "GET /report"])
  api_id             = var.api_id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.reports.id}"
  authorization_type = "JWT"
  authorizer_id      = var.authorizer_id
}

resource "aws_lambda_permission" "reports" {
  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reports.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_execution_arn}/*/*"
}

# ------------------------------------------------------------------ private S3 bucket (+ optional CloudFront)
resource "aws_s3_bucket" "web" {
  bucket        = "${var.project}-web-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "${var.project}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  count               = var.enable_cloudfront ? 1 : 0
  enabled             = true
  default_root_object = "index.html"
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }
  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = "4135ea2d-6df8-48fb-b89e-f3b8b9e3d3b1" # Managed-CachingDisabled
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "web" {
  count  = var.enable_cloudfront ? 1 : 0
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web[0].arn } }
    }]
  })
}