# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from costkb import agent_api as co
from perfkb import agent_api as p
from capacitykb import agent_api as c
from graphkb import agent_api as g
from nim_agent import cost_tools

def hdr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78)

hdr("S3. 상시부하 API용 싼 VM (도구 계층 = 사용자가 실제로 보는 것)")
print(co.recommend_specs(annotate=cost_tools._perf_annotate, footer=cost_tools._perf_footer, vcpu_min=2, mem_min_gib=4, provider="aws", region="us-east-1", limit=5))

hdr("S3b. 미추적 프로바이더 섞기")
print(co.recommend_specs(annotate=cost_tools._perf_annotate, footer=cost_tools._perf_footer, vcpu_min=2, mem_min_gib=4, limit=6))

hdr("S4. perf 프로파일")
for s in ("t3.medium", "m5.large"):
    print(f"--- {s}"); print(p.instance_profile("aws", s))

hdr("S5. 서브넷 못 바꾸는 속성")
print(c.immutable("AWS::EC2::Subnet"))

hdr("S6. describe_type")
print(g.describe_type("AWS::RDS::DBInstance")[:1200])

hdr("S7. 경계 밖 — vCPU 2000")
print(co.recommend_specs(vcpu_min=2000, mem_min_gib=4)[:900])

hdr("S8. 미러 두 얼굴 — n2-highmem-8")
print(co.recommend_specs(vcpu_min=8, mem_min_gib=60, provider="gcp", limit=3))
