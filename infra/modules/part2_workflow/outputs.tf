output "state_machine_arn" { value = aws_sfn_state_machine.approval.arn }
output "sns_topic_arn" { value = aws_sns_topic.manager.arn }
