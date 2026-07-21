# 처음부터 재생성: 리소스 그룹 → ACR → AKS 생성 후 deploy.ps1 호출.
# teardown.ps1로 전부 지운 뒤 다시 만들 때 사용.
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_config.ps1"

Write-Host "0) 리소스 프로바이더 등록..."
foreach ($p in "Microsoft.ContainerService","Microsoft.ContainerRegistry","Microsoft.Network","Microsoft.Compute") {
    az provider register -n $p | Out-Null
}
foreach ($p in "Microsoft.ContainerService","Microsoft.ContainerRegistry","Microsoft.Network","Microsoft.Compute") {
    do {
        $state = az provider show -n $p --query registrationState -o tsv
        if ($state -ne "Registered") { Write-Host "  $p = $state ..."; Start-Sleep 10 }
    } while ($state -ne "Registered")
}

Write-Host "1) 리소스 그룹..."
az group create -n $RG -l $LOC | Out-Null

Write-Host "2) ACR (Basic)..."
az acr create -g $RG -n $ACR --sku Basic | Out-Null

Write-Host "3) AKS 생성 (노드 1개, $NODE_SIZE, ACR 연동)... 3~6분 소요"
az aks create -g $RG -n $AKS --node-count 1 --node-vm-size $NODE_SIZE `
    --attach-acr $ACR --tier free --generate-ssh-keys | Out-Null
az aks get-credentials -g $RG -n $AKS --overwrite-existing | Out-Null

Write-Host "4) 빌드/배포 (deploy.ps1)..."
& "$PSScriptRoot\deploy.ps1"
