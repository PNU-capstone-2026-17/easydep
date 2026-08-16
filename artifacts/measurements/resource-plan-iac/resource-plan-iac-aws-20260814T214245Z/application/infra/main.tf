resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  tags = {
    Name = "easydep-vpc"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "main" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  tags = {
    Name = "easydep-subnet"
  }
}

resource "aws_route_table" "rt" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "a" {
  subnet_id      = aws_subnet.main.id
  route_table_id = aws_route_table.rt.id
}

resource "aws_security_group" "api" {
  name        = "api-sg"
  description = "Allow HTTP inbound"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "state" {
  name        = "state-sg"
  description = "Allow Postgres inbound from API SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_eip" "api" {
  vpc = true
  tags = {
    Name = "api-eip"
  }
}

resource "aws_instance" "api" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_api
  subnet_id                   = aws_subnet.main.id
  vpc_security_group_ids      = [aws_security_group.api.id]
  associate_public_ip_address = true
  user_data                   = templatefile("${path.module}/api_user_data.sh.tftpl", {
    api_image = var.api_image,
    db_url    = "postgres://${var.postgres_user}:${var.postgres_password}@${aws_instance.state.private_ip}:5432/${var.postgres_db}"
  })
  tags = {
    Name = "api-instance"
  }
  depends_on = [aws_eip.api]
}

resource "aws_eip_association" "api_eip_assoc" {
  instance_id   = aws_instance.api.id
  allocation_id = aws_eip.api.id
}

resource "aws_instance" "state" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type_state
  subnet_id              = aws_subnet.main.id
  vpc_security_group_ids = [aws_security_group.state.id]
  user_data = templatefile("${path.module}/state_user_data.sh.tftpl", {
    postgres_image    = "postgres:17-bookworm",
    postgres_db      = var.postgres_db,
    postgres_user    = var.postgres_user,
    postgres_password = var.postgres_password,
    data_device      = "/dev/xvdf",
    mount_point      = "/mnt/data",
    container_data_path = "/var/lib/postgresql/data"
  })
  tags = {
    Name = "state-instance"
  }
}

resource "aws_ebs_volume" "state_data" {
  availability_zone = aws_instance.state.availability_zone
  size               = 10
  type               = "gp2"
  tags = {
    Name = "state-data-volume"
  }
}

resource "aws_volume_attachment" "state_data_attach" {
  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.state_data.id
  instance_id = aws_instance.state.id
  force_detach = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*"]
  }
}

variable "api_image" {
  description = "Container image for the API runtime"
  type        = string
}

variable "instance_type_api" {
  description = "EC2 instance type for the API runtime"
  type        = string
}

variable "instance_type_state" {
  description = "EC2 instance type for the PostgreSQL state store"
  type        = string
}

variable "postgres_db" {
  description = "PostgreSQL database name"
  type        = string
}

variable "postgres_user" {
  description = "PostgreSQL user (sensitive)"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "PostgreSQL password (sensitive)"
  type        = string
  sensitive   = true
}

output "api_public_ip" {
  description = "Public IP address of the API instance"
  value       = aws_eip.api.public_ip
}

output "state_private_ip" {
  description = "Private IP address of the PostgreSQL instance"
  value       = aws_instance.state.private_ip
}
