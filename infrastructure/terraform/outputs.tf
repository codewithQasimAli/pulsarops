output "ec2_public_ip" {
  description = "Public IP of the PulsarOps EC2 instance"
  value       = aws_instance.pulsarops.public_ip
}

output "ecr_api_gateway_url" {
  description = "ECR URL for api-gateway image"
  value       = aws_ecr_repository.api_gateway.repository_url
}

output "ecr_monitor_service_url" {
  description = "ECR URL for monitor-service image"
  value       = aws_ecr_repository.monitor_service.repository_url
}

output "ecr_alert_service_url" {
  description = "ECR URL for alert-service image"
  value       = aws_ecr_repository.alert_service.repository_url
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.pulsarops.endpoint
  sensitive   = true
}

output "s3_state_bucket" {
  description = "S3 bucket for Terraform state"
  value       = aws_s3_bucket.terraform_state.bucket
}
