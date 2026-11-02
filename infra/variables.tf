variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "project" {
  type    = string
  default = "leave-mgmt"
}

variable "sender_email" {
  type        = string
  description = "SES-verified address that sends every email (verify it in SES first)"
}

variable "hr_email" {
  type        = string
  description = "Mailbox that receives HR approval requests - must match the HRAdmin employee's email"
}

variable "approval_timeout_seconds" {
  type        = number
  default     = 172800 # 48 hours. Use 120 while demoing the escalation path.
  description = "How long a manager / HR has to respond before escalation or expiry"
}

variable "enable_workflow" {
  type        = bool
  default     = true
  description = "false = Part 1 only (submit_leave will not start Step Functions)"
}

variable "enable_cloudfront" {
  type    = bool
  default = false
}