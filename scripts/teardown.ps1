# 완전 삭제: 리소스 그룹(langgraph-rg)과 그 안의 모든 것(AKS, ACR, LB, 공용 IP) 제거.
# 과금이 리소스 제거와 함께 완전히 중단된다. 다시 만들려면 provision.ps1.
# 사용: .\scripts\teardown.ps1        (확인 프롬프트)
#       .\scripts\teardown.ps1 -Yes   (프롬프트 없이)
param([switch]$Yes)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_config.ps1"

Write-Host "리소스 그룹 '$RG' 와 내부 모든 리소스를 삭제합니다 (AKS/ACR/LoadBalancer/공용IP)." -ForegroundColor Yellow
if (-not $Yes) {
    $ans = Read-Host "정말 삭제하려면 'yes' 입력"
    if ($ans -ne "yes") { Write-Host "취소됨."; return }
}

az group delete -n $RG --yes --no-wait
Write-Host "삭제 시작됨(--no-wait). 리소스가 제거되며 과금이 중단됩니다." -ForegroundColor Green
