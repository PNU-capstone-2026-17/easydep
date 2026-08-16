"""구조화 진단을 실패 소유 하위 작업으로 연결한다."""

DIAGNOSTIC_REPAIR_OWNER = {
    "APP-DEP-001": "implementation.logic",
    "APP-DB-001": "implementation.logic",
    "APP-DB-002": "implementation.logic",
    "APP-DB-003": "implementation.logic",
    "APP-DB-ENGINE-001": "implementation.logic",
    "APP-DB-MODE-001": "implementation.logic",
    "APP-STORAGE-001": "implementation.logic",
    "APP-CONFIG-001": "implementation.logic",
    "APP-COMPILE-SCAFFOLD-001": "implementation.scaffold",
    "APP-COMPILE-MEMBER-TEST-001": "implementation.scaffold",
    "APP-MEMBER-TEST-FAILURE-001": "implementation.scaffold",
    "APP-COMPILE-ACCEPTANCE-001": "implementation.acceptance_tests",
    "APP-COMPILE-LOGIC-001": "implementation.logic",
    "BIND-PORT-001": "implementation.vm_delivery",
    "BIND-STORAGE-001": "implementation.vm_delivery",
    "BIND-STORAGE-DESTRUCTIVE-INIT": "implementation.vm_delivery",
    "BIND-STORAGE-DEVICE-AMBIGUOUS": "implementation.vm_delivery",
    "BIND-HEALTH-001": "implementation.vm_delivery",
    "BIND-TLS-001": "implementation.vm_delivery",
    "CLOUD-PROJ-001": "implementation.vm_delivery",
}
