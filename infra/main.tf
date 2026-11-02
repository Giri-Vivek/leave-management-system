# Each part is a module. Deploy everything at once, or one part at a time:
#   terraform apply -target=module.part1 -var enable_workflow=false
#   terraform apply -target=module.part2
#   terraform apply            (part3 + anything left)

module "part1" {
  source                   = "./modules/part1_foundation"
  project                  = var.project
  region                   = var.region
  sender_email             = var.sender_email
  approval_timeout_seconds = var.approval_timeout_seconds
  enable_workflow          = var.enable_workflow
}

module "part2" {
  source            = "./modules/part2_workflow"
  project           = var.project
  region            = var.region
  hr_email          = var.hr_email
  common_env        = module.part1.common_env
  dynamodb_arns     = module.part1.dynamodb_arns
  zip_path          = module.part1.zip_path
  zip_hash          = module.part1.zip_hash
  api_id            = module.part1.api_id
  api_execution_arn = module.part1.api_execution_arn
  api_endpoint      = module.part1.api_endpoint
}

module "part3" {
  source            = "./modules/part3_frontend"
  project           = var.project
  region            = var.region
  common_env        = module.part1.common_env
  dynamodb_arns     = module.part1.dynamodb_arns
  zip_path          = module.part1.zip_path
  zip_hash          = module.part1.zip_hash
  api_id            = module.part1.api_id
  api_execution_arn = module.part1.api_execution_arn
  authorizer_id     = module.part1.authorizer_id
  enable_cloudfront = var.enable_cloudfront
}
