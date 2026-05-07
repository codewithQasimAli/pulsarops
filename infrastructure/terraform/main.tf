# ── Data sources ──────────────────────────────────────────────────────────────
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── VPC ───────────────────────────────────────────────────────────────────────
resource "aws_vpc" "pulsarops" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "pulsarops-vpc"
    Environment = var.environment
  }
}

resource "aws_internet_gateway" "pulsarops" {
  vpc_id = aws_vpc.pulsarops.id

  tags = {
    Name = "pulsarops-igw"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.pulsarops.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "pulsarops-public-subnet"
  }
}

resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.pulsarops.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = data.aws_availability_zones.available.names[1]

  tags = {
    Name = "pulsarops-private-subnet"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.pulsarops.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pulsarops.id
  }

  tags = {
    Name = "pulsarops-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Security Groups ───────────────────────────────────────────────────────────
resource "aws_security_group" "ec2" {
  name        = "pulsarops-ec2-sg"
  description = "Security group for PulsarOps EC2 instance"
  vpc_id      = aws_vpc.pulsarops.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "k3s API server"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "PulsarOps services"
    from_port   = 8000
    to_port     = 8012
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Prometheus"
    from_port   = 9090
    to_port     = 9091
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Grafana"
    from_port   = 3000
    to_port     = 3001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "pulsarops-ec2-sg"
  }
}

resource "aws_security_group" "rds" {
  name        = "pulsarops-rds-sg"
  description = "Security group for PulsarOps RDS PostgreSQL"
  vpc_id      = aws_vpc.pulsarops.id

  ingress {
    description     = "PostgreSQL from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }

  tags = {
    Name = "pulsarops-rds-sg"
  }
}

# ── EC2 Instance ──────────────────────────────────────────────────────────────
resource "aws_instance" "pulsarops" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ec2.id]

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y curl wget git unzip
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker ubuntu
    curl -sfL https://get.k3s.io | sh -
    chmod 644 /etc/rancher/k3s/k3s.yaml
  EOF

  tags = {
    Name        = "pulsarops-server"
    Environment = var.environment
  }
}

# ── ECR Repositories ──────────────────────────────────────────────────────────
resource "aws_ecr_repository" "api_gateway" {
  name                 = "pulsarops/api-gateway"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "pulsarops-api-gateway"
  }
}

resource "aws_ecr_repository" "monitor_service" {
  name                 = "pulsarops/monitor-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "pulsarops-monitor-service"
  }
}

resource "aws_ecr_repository" "alert_service" {
  name                 = "pulsarops/alert-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "pulsarops-alert-service"
  }
}

# ── S3 for Terraform state ────────────────────────────────────────────────────
resource "aws_s3_bucket" "terraform_state" {
  bucket = "pulsarops-terraform-state"

  tags = {
    Name = "pulsarops-terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ── RDS PostgreSQL ────────────────────────────────────────────────────────────
resource "aws_db_subnet_group" "pulsarops" {
  name       = "pulsarops-db-subnet-group"
  subnet_ids = [aws_subnet.public.id, aws_subnet.private.id]

  tags = {
    Name = "pulsarops-db-subnet-group"
  }
}

resource "aws_db_instance" "pulsarops" {
  identifier        = "pulsarops-postgres"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "pulsarops"
  username = "pulsarops"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.pulsarops.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  skip_final_snapshot = true
  publicly_accessible = false

  tags = {
    Name        = "pulsarops-postgres"
    Environment = var.environment
  }
}
