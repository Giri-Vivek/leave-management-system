output "api_endpoint" { value = module.part1.api_endpoint }
output "user_pool_id" { value = module.part1.user_pool_id }
output "client_id" { value = module.part1.client_id }
output "state_machine_arn" { value = module.part2.state_machine_arn }
output "website_url" { value = module.part3.website_url }
output "web_bucket" { value = module.part3.bucket }
output "region" { value = var.region }
