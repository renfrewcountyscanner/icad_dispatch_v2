from lib.address_extractor_module import AddressExtractorLLM, is_dispatch_unit_label


def test_dispatch_base_is_not_a_city_or_geocode_component():
    extractor = AddressExtractorLLM.__new__(AddressExtractorLLM)
    extractor.log = __import__("logging").getLogger("test")

    result = extractor._to_result({
        "raw_text": "12 Ren Drive, Pembroke Base, ON",
        "street": "12 Ren Drive",
        "city": "Pembroke Base",
        "state": "ON",
        "confidence": 1.0,
    })

    assert result is not None
    assert result.city is None
    assert result.raw_text == "12 Ren Drive, ON"


def test_real_city_name_is_preserved():
    assert is_dispatch_unit_label("Pembroke") is False
    assert is_dispatch_unit_label("Pembroke Base") is True
