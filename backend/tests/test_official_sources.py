from pathlib import Path

from app.services.official_sources import OfficialSourceRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = (
    PROJECT_ROOT / "data" / "reference" / "official_legal_sources.json"
)


def test_official_source_registry_contains_all_16_item_ids():
    registry = OfficialSourceRegistry.from_path(MAPPING_PATH)

    assert len(registry.records) == 16
    assert {record.item_id for record in registry.records} == {
        "139264",
        "146696",
        "148825",
        "152668",
        "152669",
        "152670",
        "152734",
        "152735",
        "153297",
        "155202",
        "158798",
        "162330",
        "163444",
        "167029",
        "169619",
        "183939",
    }
    assert all(record.canonical_url.startswith("https://") for record in registry.records)


def test_registry_resolves_primary_document_before_reference_urls():
    registry = OfficialSourceRegistry.from_path(MAPPING_PATH)

    record = registry.resolve({
        "source_document_id": "vbpl:item:152668",
        "source_urls": [
            "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=158798"
        ],
    })

    assert record is not None
    assert record.document_number == "145/2020/NĐ-CP"
    assert record.canonical_url.endswith("docid=201967&pageid=27160")


def test_registry_resolves_item_id_from_legacy_url():
    registry = OfficialSourceRegistry.from_path(MAPPING_PATH)

    record = registry.resolve({
        "source_urls": [
            "http://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=139264#Dieu_169"
        ]
    })

    assert record is not None
    assert record.document_number == "45/2019/QH14"
    assert record.url_status == "active"


def test_2023_labor_circular_uses_working_government_url():
    registry = OfficialSourceRegistry.from_path(MAPPING_PATH)
    record = registry.resolve({"source_document_id": "vbpl:item:167029"})

    assert record is not None
    assert record.canonical_url == (
        "https://vanban.chinhphu.vn/"
        "?classid=1&docid=209584&pageid=27160"
    )
