# 시작: 중지된 AKS 클러스터를 재개하고 앱 URL을 출력한다.
# (aks-stop.ps1로 멈춘 상태에서 다시 켤 때 사용. 리소스는 그대로 유지되므로 재배포 불필요)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_config.ps1"

Write-Host "AKS '$AKS' 시작 중... (노드 VM 재할당, 2~3분 소요)"
az aks start -g $RG -n $AKS

Write-Host "kubeconfig 갱신..."
az aks get-credentials -g $RG -n $AKS --overwrite-existing | Out-Null

Write-Host "파드 준비 대기..."
kubectl rollout status deployment/$DEPLOY --timeout=300s

Show-AppUrl
