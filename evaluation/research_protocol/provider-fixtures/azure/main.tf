terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "=5.0.1"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "admin_password" {
  type      = string
  sensitive = true
  default   = "AuditOnly-NotDeployed-ChangeMe1!"
}

resource "azurerm_resource_group" "main" {
  name     = "easydep-component-audit"
  location = "Korea Central"
}

resource "azurerm_virtual_network" "main" {
  name                = "easydep-component-audit"
  address_space       = ["10.81.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_subnet" "application_gateway" {
  name                 = "application-gateway"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.81.1.0/24"]
}

resource "azurerm_subnet" "application" {
  name                 = "application"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.81.2.0/24"]
}

resource "azurerm_public_ip" "gateway" {
  name                = "easydep-component-audit"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "app_1" {
  name                = "easydep-component-audit-1"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.application.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface" "app_2" {
  name                = "easydep-component-audit-2"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.application.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "app_1" {
  name                            = "easydep-component-audit-1"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  size                            = "Standard_B2s"
  zone                            = "1"
  admin_username                  = "easydep"
  disable_password_authentication = false
  admin_password                  = var.admin_password
  network_interface_ids           = [azurerm_network_interface.app_1.id]

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }
}

resource "azurerm_linux_virtual_machine" "app_2" {
  name                            = "easydep-component-audit-2"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  size                            = "Standard_B2s"
  zone                            = "2"
  admin_username                  = "easydep"
  disable_password_authentication = false
  admin_password                  = var.admin_password
  network_interface_ids           = [azurerm_network_interface.app_2.id]

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }
}

resource "azurerm_managed_disk" "notes" {
  name                 = "easydep-component-audit-notes"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  storage_account_type = "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = 20
  zone                 = "1"
}

resource "azurerm_virtual_machine_data_disk_attachment" "notes" {
  managed_disk_id    = azurerm_managed_disk.notes.id
  virtual_machine_id = azurerm_linux_virtual_machine.app_1.id
  lun                = 0
  caching            = "ReadWrite"
}

resource "azurerm_lb" "app" {
  name                = "easydep-component-audit-l4"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"

  frontend_ip_configuration {
    name                 = "public"
    public_ip_address_id = azurerm_public_ip.gateway.id
  }
}

resource "azurerm_lb_backend_address_pool" "app" {
  name            = "application"
  loadbalancer_id = azurerm_lb.app.id
}

resource "azurerm_lb_probe" "app" {
  name                = "health"
  loadbalancer_id     = azurerm_lb.app.id
  protocol            = "Http"
  port                = 8080
  request_path        = "/health"
}

resource "azurerm_lb_rule" "app" {
  name                           = "http-over-tcp"
  loadbalancer_id                = azurerm_lb.app.id
  protocol                       = "Tcp"
  frontend_port                  = 80
  backend_port                   = 8080
  frontend_ip_configuration_name = "public"
  backend_address_pool_ids       = [azurerm_lb_backend_address_pool.app.id]
  probe_id                       = azurerm_lb_probe.app.id
}

resource "azurerm_network_interface_backend_address_pool_association" "app_1_l4" {
  network_interface_id    = azurerm_network_interface.app_1.id
  ip_configuration_name   = "primary"
  backend_address_pool_id = azurerm_lb_backend_address_pool.app.id
}

resource "azurerm_network_interface_backend_address_pool_association" "app_2_l4" {
  network_interface_id    = azurerm_network_interface.app_2.id
  ip_configuration_name   = "primary"
  backend_address_pool_id = azurerm_lb_backend_address_pool.app.id
}

resource "azurerm_application_gateway" "app" {
  name                = "easydep-component-audit"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  sku {
    name     = "Standard_v2"
    tier     = "Standard_v2"
    capacity = 2
  }

  gateway_ip_configuration {
    name      = "gateway"
    subnet_id = azurerm_subnet.application_gateway.id
  }

  frontend_ip_configuration {
    name                 = "public"
    public_ip_address_id = azurerm_public_ip.gateway.id
  }

  frontend_port {
    name = "http"
    port = 80
  }

  frontend_port {
    name = "https"
    port = 443
  }

  backend_address_pool {
    name = "application"
  }

  probe {
    name                = "health"
    protocol            = "Http"
    path                = "/health"
    interval            = 30
    timeout             = 10
    unhealthy_threshold = 3
  }

  backend_http_settings {
    name                  = "application"
    cookie_based_affinity = "Disabled"
    port                  = 8080
    protocol              = "Http"
    request_timeout       = 30
    probe_name            = "health"
  }

  ssl_certificate {
    name     = "audit-only"
    data     = base64encode("not-a-deployable-pfx")
    password = "not-a-secret"
  }

  http_listener {
    name                           = "http"
    frontend_ip_configuration_name = "public"
    frontend_port_name             = "http"
    protocol                       = "Http"
  }

  http_listener {
    name                           = "https"
    frontend_ip_configuration_name = "public"
    frontend_port_name             = "https"
    protocol                       = "Https"
    ssl_certificate_name           = "audit-only"
  }

  request_routing_rule {
    name                       = "http"
    priority                   = 100
    rule_type                  = "Basic"
    http_listener_name         = "http"
    backend_address_pool_name  = "application"
    backend_http_settings_name = "application"
  }

  request_routing_rule {
    name                       = "https"
    priority                   = 110
    rule_type                  = "Basic"
    http_listener_name         = "https"
    backend_address_pool_name  = "application"
    backend_http_settings_name = "application"
  }
}

resource "azurerm_network_interface_application_gateway_backend_address_pool_association" "app_1" {
  network_interface_id    = azurerm_network_interface.app_1.id
  ip_configuration_name   = "primary"
  backend_address_pool_id = one(azurerm_application_gateway.app.backend_address_pool).id
}

resource "azurerm_network_interface_application_gateway_backend_address_pool_association" "app_2" {
  network_interface_id    = azurerm_network_interface.app_2.id
  ip_configuration_name   = "primary"
  backend_address_pool_id = one(azurerm_application_gateway.app.backend_address_pool).id
}
