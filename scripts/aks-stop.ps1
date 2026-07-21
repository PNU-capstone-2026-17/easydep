# 종료: AKS 노드 VM을 할당 해제해 컴퓨트 과금을 멈춘다.
# 클러스터/ACR/LoadBalancer 리소스는 유지되므로 aks-start.ps1로 빠르게 재개 가능.
# 주의: LoadBalancer 공용 IP는 정지 중에도 소액 과금됨. 완전 삭제는 teardown.ps1.
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_config.ps1"

Write-Host "AKS '$AKS' 중지 중... (노드 VM 할당 해제, 컴퓨트 과금 중단)"
az aks stop -g $RG -n $AKS

Write-Host "완료. 재개하려면 scripts\aks-start.ps1, 완전 삭제는 scripts\teardown.ps1" -ForegroundColor Green
