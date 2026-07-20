# 공용 설정 — 다른 스크립트에서 dot-source( . )로 읽어들인다.
# 값 변경 시 여기만 수정하면 모든 스크립트에 반영된다.

# --- 콘솔 인코딩을 UTF-8로 고정 (한글 출력 깨짐 방지) ---
# 모든 스크립트가 이 파일을 dot-source 하므로 여기서 한 번만 설정한다.
# 전제: .ps1 파일들은 UTF-8 BOM으로 저장돼 있어야 Windows PowerShell 5.1이 한글 리터럴을
#       ANSI(cp949)로 오해하지 않고 바르게 읽는다. (BOM 없으면 이 설정만으론 안 고쳐짐)
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8   # 네이티브 도구(az/kubectl)로의 파이프도 UTF-8
    if (Get-Command chcp -ErrorAction SilentlyContinue) { chcp 65001 | Out-Null }
} catch { }

$RG        = "langgraph-rg"
$LOC       = "koreacentral"
$ACR       = "langgraphacr1"          # ACR 이름 (전역 유일)
$AKS       = "langgraph-aks"
$IMAGE     = "langgraph-chatbot"
$TAG       = "v1"
$NODE_SIZE = "Standard_B2s_v2"          # Azure for Students: B2s(v1) 불가 → v2 사용
$DEPLOY    = "langgraph-chatbot"        # Deployment/Service 이름
$IMAGE_REF = "${ACR}.azurecr.io/${IMAGE}:${TAG}"

# 앱 외부 URL을 출력하는 헬퍼
function Show-AppUrl {
    $ip = kubectl get svc $DEPLOY -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>$null
    if ($ip) { Write-Host "`n앱 URL:  http://$ip`n" -ForegroundColor Green }
    else     { Write-Host "`nLoadBalancer 외부 IP 아직 할당 전. 잠시 후 'kubectl get svc $DEPLOY' 확인." -ForegroundColor Yellow }
}
