variable "project" { type = string }
variable "region" { type = string }
variable "common_env" { type = map(string) }
variable "dynamodb_arns" { type = list(string) }
variable "zip_path" { type = string }
variable "zip_hash" { type = string }
variable "api_id" { type = string }
variable "api_execution_arn" { type = string }
variable "authorizer_id" { type = string }
variable "enable_cloudfront" {
  type    = bool
  default = true
}