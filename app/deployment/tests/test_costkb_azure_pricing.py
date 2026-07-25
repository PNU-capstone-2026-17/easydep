"""Azure Retail Prices → 스팟·예약·저축 플랜.

**함정 셋을 실측으로 겪었고, 셋 다 조용히 틀린 값을 만들었을 것이다.** 여기 테스트는
그 셋과, 이 소스만 갖는 계약(재배포 금지)을 고정한다.

    1  같은 SKU에 Windows 값이 따로 있다        → 라이선스 값이 섞여 두 배 가까이 뛴다
    2  Cloud Services가 같은 크기 이름을 쓴다   → 1+2를 안 거르면 SKU의 93.4%가 값이 여럿
    3  예약가는 기간 총액인데 단위 칸은 '1 Hour' → 그대로 읽으면 5,165배 틀린다
"""

from __future__ import annotations

import json

import pytest

from costkb import dataset
from costkb.agent_api import AZURE_DISCOUNT_MISSING, azure_discount_pricing
from costkb.parsers.azure_pricing import TERM_HOURS, _index, _is_vm, kind_of

VM = "Standard_B2s_v2"


def _row(**over) -> dict:
    row = {
        "armSkuName": VM,
        "productName": "Virtual Machines Bsv2 Series",
        "skuName": "B2s v2",
        "meterName": "B2s v2",
        "type": "Consumption",
        "retailPrice": 0.1,
        "reservationTerm": None,
        "isPrimaryMeterRegion": True,
    }
    row.update(over)
    return row


# --- 함정 1·2: 같은 SKU에 다른 제품·OS가 섞여 있다 ---------------------------

def test_windows_is_not_a_vm_price() -> None:
    assert not _is_vm(_row(productName="Virtual Machines Bsv2 Series Windows"))


def test_abbreviated_windows_tail_is_caught_too() -> None:
    """**`endswith("Windows")`만 보면 하나를 놓친다.**

    전 리전 빌드에서 `standard_ncc40ads_h100_v5` 세 리전이 "값이 여럿"으로 걸렸고,
    범인은 `Virtual Machines NCCadsv5 Srs Win`이었다 — Windows가 `Win`으로 줄어
    있었다. 가드가 아니었으면 7.89 대신 9.73(Windows)이 조용히 들어갔을 것이다.
    """
    assert not _is_vm(_row(productName="Virtual Machines NCCadsv5 Srs Win"))


def test_a_series_name_containing_win_mid_string_still_passes() -> None:
    """꼬리 토큰만 본다 — 'Win'이 시리즈 이름 안에 들어간 것까지 거르면 과잉이다."""
    assert _is_vm(_row(productName="Virtual Machines Winsome Series"))


def test_cloud_services_is_not_a_vm() -> None:
    """**구형 PaaS가 같은 크기 이름을 쓴다.** 실측 0.6130(VM) vs 0.6860(Cloud Services)."""
    assert not _is_vm(_row(productName="Bsv2 Series Cloud Services"))


def test_plain_vm_product_passes() -> None:
    assert _is_vm(_row())


def test_ambiguous_sku_is_dropped_not_guessed() -> None:
    """거를 것을 다 거르면 값이 여럿인 경우가 0이다(실측).

    그러니 여럿이 나오면 **우리가 모르는 축이 생겼다**는 신호다. 조용히 하나를
    고르면 그 사실이 묻히고, 어느 값이 잡히는지는 응답 순서가 정한다.
    """
    by_sku, _ = _index([_row(retailPrice=0.1), _row(retailPrice=0.2)])
    assert len(by_sku[VM.lower()]["ondemand"]) == 2


# --- 함정 3: 단위 칸이 거짓말한다 ---------------------------------------------

def test_reservation_is_a_term_total_not_an_hourly_rate() -> None:
    """**가장 위험한 함정.** 1,348건 전부 unitOfMeasure='1 Hour'라고 적혀 있다."""
    rows = [
        _row(),
        _row(type="Reservation", reservationTerm="1 Year",
             retailPrice=0.59 * TERM_HOURS["1 Year"], unitOfMeasure="1 Hour"),
    ]
    by_sku, _ = _index(rows)
    hourly = next(iter(by_sku[VM.lower()]["reserved:1 Year"]))
    assert hourly == pytest.approx(0.59)


def test_savings_plan_is_already_hourly_and_is_not_divided() -> None:
    """**같은 응답 안에서 두 단위가 섞여 있다.** 한 규칙으로 처리하면 한쪽이 틀린다."""
    rows = [_row(savingsPlan=[{"term": "3 Years", "retailPrice": 0.047}])]
    by_sku, _ = _index(rows)
    assert next(iter(by_sku[VM.lower()]["savings:3 Years"])) == pytest.approx(0.047)


def test_unknown_reservation_term_is_dropped() -> None:
    """실측에 '10 Years'가 한 건 있었다. 시간 수를 모르면 환산할 수 없다."""
    _, dropped = _index([_row(type="Reservation", reservationTerm="10 Years",
                              retailPrice=99999)])
    assert dropped["unknown-term"] == 1


# --- 담지 않는 것 -------------------------------------------------------------

def test_devtest_and_low_priority_are_counted_not_carried() -> None:
    """Dev/Test는 특수 구독에서만, Low Priority는 적용 조건을 알 수 없다."""
    rows = [
        _row(type="DevTestConsumption"),
        _row(skuName="B2s v2 Low Priority", meterName="B2s v2 Low Priority"),
    ]
    by_sku, dropped = _index(rows)
    assert by_sku == {}
    assert dropped["devtest"] == 1 and dropped["lowpriority"] == 1


def test_spot_is_read_from_the_name_not_the_type() -> None:
    """스팟은 별도 `type`이 아니다 — `Consumption`인 채로 이름에 Spot이 붙는다."""
    assert kind_of(_row(skuName="B2s v2 Spot", meterName="B2s v2 Spot")) == "spot"


# --- 이 소스만 갖는 계약: 저장소에 없는 것이 기본 -----------------------------

def test_committed_artifact_discloses_its_redistribution_status() -> None:
    """**허가가 없는 것과 명시하는 것은 다른 문제다.**

    출처를 밝힌다고 라이선스가 생기지는 않는다. 저장소에 넣기로 한 이상, 파일 하나만
    떼어 봐도 어디서 왔고 허가가 어떤 상태인지 보여야 한다 — 산출물은 저장소 밖으로도
    나간다. `NOTICE` 쪽 강제는 `test_redistribution_notice.py`에 있다.
    """
    from kbcommon import artifact

    packed = artifact.BUNDLED_DIR / f"{dataset.AZURE_DISCOUNT_FILENAME}.gz"
    if not packed.exists():
        pytest.skip("아직 빌드·포장하지 않은 환경")
    note = " ".join((artifact.load_json(packed).get("_note") or "").split()).lower()
    assert "redistribution" in note
    assert "found no wording granting redistribution" in note
    assert "does not mean permission was granted" in note


def test_missing_artifact_says_how_to_get_it_not_that_there_is_no_discount(tmp_path) -> None:
    """**빈 답은 '할인이 없다'로 읽힌다.**

    이제 `data/`에 포장돼 있어 보통은 안 나오지만, 다른 `output_dir`를 명시하면
    폴백이 없으므로 여전히 이 경로를 탄다.
    """
    dataset.clear_caches()
    try:
        text = azure_discount_pricing("Standard_D2s_v5", "koreasouth", output_dir=tmp_path)
        assert text == AZURE_DISCOUNT_MISSING
        flat = " ".join(text.split())
        assert "build-azure-pricing" in flat
        assert "not that there are no discounts" in flat
    finally:
        dataset.clear_caches()


# --- 조회 ---------------------------------------------------------------------

@pytest.fixture()
def built(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / dataset.AZURE_DISCOUNT_FILENAME).write_text(json.dumps({
        "records": [
            {"specName": "standard_d2s_v5", "region": "koreasouth",
             "ondemandUSD": 0.11, "mirrorUSD": 0.11, "matchesMirror": True,
             "spotUSD": 0.0203, "reserved1yUSD": 0.0682, "reserved3yUSD": 0.0436,
             "savings1yUSD": 0.0836, "savings3yUSD": 0.0605},
        ],
        "_source": [{"source": "azure-retail-prices", "pin_kind": "digest",
                     "pin": "(고정 불가)"}],
    }, ensure_ascii=False), encoding="utf-8")
    dataset.clear_caches()
    yield out
    dataset.clear_caches()


def test_answer_carries_the_share_not_just_the_number(built) -> None:
    """값만 주면 얼마나 싼지 사용자가 암산해야 하고, 그 암산은 우리가 보증하지 않는다."""
    text = azure_discount_pricing("Standard_D2s_v5", "koreasouth", output_dir=built)
    assert "0.0203" in text and "18%" in text


def test_answer_discloses_the_unit_conversion(built) -> None:
    """환산했다는 사실이 빠지면 사용자가 원본과 대조할 때 안 맞는다."""
    text = azure_discount_pricing("Standard_D2s_v5", "koreasouth", output_dir=built)
    flat = " ".join(text.split())
    assert "period totals in the source" in flat and "converted here to hourly" in flat


def test_case_insensitive_join(built) -> None:
    """API는 `Standard_D2s_v5`, 색인은 소문자 — 대소문자로 조인이 깨진 적이 세 번 있다."""
    assert dataset.azure_discount_for("STANDARD_D2S_V5", "koreasouth", built) is not None


def test_unknown_spec_says_dataset_not_world(built) -> None:
    text = azure_discount_pricing("Standard_Nope", "koreasouth", output_dir=built)
    assert "it is not in this dataset" in " ".join(text.split())


def test_suggestions_stay_inside_this_dataset(built) -> None:
    """**AWS 이름을 권하면 그게 Azure에 있는 것처럼 읽힌다.**

    처음엔 미러 전체에서 찾아서, `m5.large`에 m5n.large·m5d.large를 줄줄이 권했다.
    """
    text = azure_discount_pricing("m5.large", "koreasouth", output_dir=built)
    flat = " ".join(text.split())
    assert "it is not in this dataset" in flat
    assert "m5n.large" not in flat and "m5d.large" not in flat
