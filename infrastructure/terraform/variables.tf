variable "aws_region" {
  description = "AWS region to deploy PulsarOps infrastructure"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "instance_type" {
  description = "EC2 instance type — t3.small is free tier for accounts created after July 15 2025"
  type        = string
  default     = "t3.small"
}

variable "db_instance_class" {
  description = "RDS instance class — db.t3.micro is free tier eligible"
  type        = string
  default     = "db.t3.micro"
}

variable "db_password" {
  description = "PostgreSQL master password"
  type        = string
  sensitive   = true
}
