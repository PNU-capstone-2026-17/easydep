resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_virtual_network" "vnet" {
  name                = "easydep-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_subnet" "subnet" {
  name                 = "easydep-subnet"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "nsg_api" {
  name                = "nsg-api"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name

  security_rule {
    name                       = "allow-http"
    priority                   = 1001
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = tostring(var.api_port)
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_network_security_group" "nsg_state" {
  name                = "nsg-state"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name

  security_rule {
    name                       = "allow-postgres-from-subnet"
    priority                   = 1001
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = tostring(var.postgres_port)
    source_address_prefix      = azurerm_subnet.subnet.address_prefixes[0]
    destination_address_prefix = "*"
  }
}

resource "azurerm_public_ip" "api_ip" {
  name                = "api-public-ip"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "api_nic" {
  name                = "api-nic"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.subnet.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.api_ip.id
  }
}

resource "azurerm_network_interface_security_group_association" "api_nic_nsg" {
  network_interface_id      = azurerm_network_interface.api_nic.id
  network_security_group_id = azurerm_network_security_group.nsg_api.id
}

resource "azurerm_network_interface" "state_nic" {
  name                = "state-nic"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.subnet.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface_security_group_association" "state_nic_nsg" {
  network_interface_id      = azurerm_network_interface.state_nic.id
  network_security_group_id = azurerm_network_security_group.nsg_state.id
}

resource "azurerm_linux_virtual_machine" "api_vm" {
  name                = "api-vm"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  size                = var.api_vm_size
  admin_username      = var.admin_username
  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_key
  }
  network_interface_ids = [azurerm_network_interface.api_nic.id]
  source_image_reference {
    publisher = "Canonical"
    offer     = "UbuntuServer"
    sku       = "22.04-LTS"
    version   = "latest"
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }
  custom_data = base64encode(templatefile("cloud_init_api.sh.tftpl", {
    api_port           = var.api_port,
    api_container_image = var.api_container_image
  }))
}

resource "azurerm_managed_disk" "state_disk" {
  name                 = "state-data-disk"
  location             = var.location
  resource_group_name  = azurerm_resource_group.rg.name
  storage_account_type = "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = 30
}

resource "azurerm_virtual_machine_data_disk_attachment" "state_disk_attach" {
  managed_disk_id    = azurerm_managed_disk.state_disk.id
  virtual_machine_id = azurerm_linux_virtual_machine.state_vm.id
  lun                = 0
  caching            = "ReadWrite"
}

resource "azurerm_linux_virtual_machine" "state_vm" {
  name                = "state-vm"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  size                = var.state_vm_size
  admin_username      = var.admin_username
  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_key
  }
  network_interface_ids = [azurerm_network_interface.state_nic.id]
  source_image_reference {
    publisher = "Canonical"
    offer     = "UbuntuServer"
    sku       = "22.04-LTS"
    version   = "latest"
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }
  custom_data = base64encode(templatefile("cloud_init_state.sh.tftpl", {
    postgres_port   = var.postgres_port,
    postgres_image  = var.postgres_image,
    postgres_db     = var.postgres_db,
    postgres_user   = var.postgres_user,
    postgres_password = var.postgres_password
  }))
}

output "api_public_ip" {
  description = "Public IP address of the API runtime"
  value       = azurerm_public_ip.api_ip.ip_address
}

output "api_endpoint" {
  description = "HTTP endpoint for the API"
  value       = "http://${azurerm_public_ip.api_ip.ip_address}:${var.api_port}"
}
