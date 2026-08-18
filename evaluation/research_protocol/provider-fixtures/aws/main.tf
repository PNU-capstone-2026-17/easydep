terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "=5.100.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"
}

data "aws_acm_certificate" "service" {
  domain      = "component-audit.example.invalid"
  statuses    = ["ISSUED"]
  most_recent = true
}

resource "aws_vpc" "main" {
  cidr_block = "10.80.0.0/16"
}

resource "aws_subnet" "zone_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.80.1.0/24"
  availability_zone = "ap-northeast-2a"
}

resource "aws_subnet" "zone_c" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.80.2.0/24"
  availability_zone = "ap-northeast-2c"
}

resource "aws_security_group" "lb" {
  name   = "easydep-component-audit-lb"
  vpc_id = aws_vpc.main.id
}

resource "aws_instance" "app_a" {
  ami               = "ami-00000000000000000"
  instance_type     = "t3.micro"
  availability_zone = aws_subnet.zone_a.availability_zone
  subnet_id         = aws_subnet.zone_a.id
}

resource "aws_instance" "app_c" {
  ami               = "ami-00000000000000000"
  instance_type     = "t3.micro"
  availability_zone = aws_subnet.zone_c.availability_zone
  subnet_id         = aws_subnet.zone_c.id
}

resource "aws_ebs_volume" "notes" {
  availability_zone = aws_instance.app_a.availability_zone
  size              = 20
  type              = "gp3"
}

resource "aws_volume_attachment" "notes" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.notes.id
  instance_id = aws_instance.app_a.id
}

resource "aws_lb" "app" {
  name               = "easydep-component-audit"
  internal           = false
  load_balancer_type = "network"
  security_groups    = [aws_security_group.lb.id]
  subnets            = [aws_subnet.zone_a.id, aws_subnet.zone_c.id]
}

resource "aws_lb_target_group" "app" {
  name        = "easydep-component-audit"
  port        = 8080
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  health_check {
    path = "/health"
  }
}

resource "aws_lb_target_group_attachment" "app_a" {
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = aws_instance.app_a.id
  port             = 8080
}

resource "aws_lb_target_group_attachment" "app_c" {
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = aws_instance.app_c.id
  port             = 8080
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "TLS"
  certificate_arn   = data.aws_acm_certificate.service.arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
