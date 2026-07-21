# 재배포: 코드 변경 후 이미지 빌드 → ACR push → AKS 재배포.
# (클러스터/ACR는 이미 존재한다고 가정. 처음부터 만들려면 provision.ps1)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_config.ps1"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    if (-not (Test-Path ".env")) { throw ".env 파일이 없습니다." }

    Write-Host "1) 이미지 빌드 (linux/amd64)..."
    docker build --platform linux/amd64 --provenance=false -t $IMAGE_REF .

    Write-Host "2) ACR 로그인 + push..."
    az acr login -n $ACR
    docker push $IMAGE_REF

    Write-Host "3) 시크릿 갱신 (.env 기반)..."
    kubectl delete secret nim-secret --ignore-not-found | Out-Null
    kubectl create secret generic nim-secret --from-env-file=.env | Out-Null

    Write-Host "4) 매니페스트 적용 + 롤아웃..."
    kubectl apply -k k8s/overlays/aks
    kubectl rollout restart deployment/$DEPLOY
    kubectl rollout status deployment/$DEPLOY --timeout=300s

    Show-AppUrl
}
finally { Pop-Location }
