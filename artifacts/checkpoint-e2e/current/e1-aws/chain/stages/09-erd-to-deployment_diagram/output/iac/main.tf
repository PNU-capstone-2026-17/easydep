resource "aws_ecr_repository" "registry_course_registration_app" {
  name                 = "${var.resource_prefix}-registry_course_registration_app"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
}

resource "aws_iam_role" "registry_pull_identity_compute_1" {
  name = "${var.resource_prefix}-registry_pull_identity_compute_1"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ec2.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "registry_instance_profile_compute_1" {
  name = "${var.resource_prefix}-registry_instance_profile_compute_1"
  role = aws_iam_role.registry_pull_identity_compute_1.name
}

resource "aws_iam_role_policy_attachment" "registry_pull_binding_compute_1_course_registration_app" {
  role       = aws_iam_role.registry_pull_identity_compute_1.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "secret_access_binding_course_registration_app_course_registration_db_credential" {
  name = "${var.resource_prefix}-secret_access_binding_course_registration_app_course_registration_db_credential"
  role = aws_iam_role.registry_pull_identity_compute_1.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.secret_reference_course_registration_app_course_registration_db_credential
    }]
  })
}

resource "aws_iam_role_policy" "secret_access_binding_course_registration_db_runtime_credential" {
  name = "${var.resource_prefix}-secret_access_binding_course_registration_db_runtime_credential"
  role = aws_iam_role.registry_pull_identity_compute_1.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.secret_reference_course_registration_db_runtime_credential
    }]
  })
}

resource "aws_vpc" "network" {
  cidr_block           = "10.80.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
}

resource "aws_internet_gateway" "internet_gateway" {
  vpc_id = aws_vpc.network.id
}

resource "aws_route_table" "public_route_table" {
  vpc_id = aws_vpc.network.id
}

resource "aws_route" "public_default_route" {
  route_table_id         = aws_route_table.public_route_table.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.internet_gateway.id
}

resource "aws_subnet" "subnet_compute_1_1" {
  vpc_id                  = aws_vpc.network.id
  cidr_block              = "10.80.1.0/24"
  map_public_ip_on_launch = true
}

resource "aws_route_table_association" "route_association_compute_1_1" {
  subnet_id      = aws_subnet.subnet_compute_1_1.id
  route_table_id = aws_route_table.public_route_table.id
}

resource "aws_security_group" "traffic_filter_compute_1" {
  name_prefix = "${var.resource_prefix}-traffic_filter_compute_1-"
  vpc_id      = aws_vpc.network.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "compute_1" {
  ami                    = var.boot_image_id
  instance_type          = var.vm_sku
  subnet_id              = aws_subnet.subnet_compute_1_1.id
  vpc_security_group_ids = [aws_security_group.traffic_filter_compute_1.id]
  user_data = templatefile("${path.module}/bootstrap_compute_1.sh.tftpl", {
    disk_id_course_registration_db_data = aws_ebs_volume.data_disk_course_registration_db_data.id, registry_course_registration_app = aws_ecr_repository.registry_course_registration_app.repository_url, image_digest_course_registration_app = var.image_digest_course_registration_app, port_course_registration_app_course_registration_app_http = var.container_port_course_registration_app_course_registration_app_http, secret_ref_course_registration_app_course_registration_db_credential = var.secret_reference_course_registration_app_course_registration_db_credential, secret_ref_course_registration_db_runtime_credential = var.secret_reference_course_registration_db_runtime_credential
  })
  private_ip           = "10.80.1.10"
  iam_instance_profile = aws_iam_instance_profile.registry_instance_profile_compute_1.name
}

resource "aws_eip" "public_ip_compute_1" {
  domain   = "vpc"
  instance = aws_instance.compute_1.id
}

resource "aws_ebs_volume" "data_disk_course_registration_db_data" {
  availability_zone = aws_subnet.subnet_compute_1_1.availability_zone
  size              = 20
  type              = "gp3"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "disk_attachment_course_registration_db_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data_disk_course_registration_db_data.id
  instance_id = aws_instance.compute_1.id
}
