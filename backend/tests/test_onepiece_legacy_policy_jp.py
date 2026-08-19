from app.onepiece_legacy_policy import is_onepiece_official_image


def test_onepiece_official_image_accepts_english_and_japanese_hosts():
    assert is_onepiece_official_image("https://en.onepiece-cardgame.com/images/cardlist/card/OP16-119.png")
    assert is_onepiece_official_image("https://www.onepiece-cardgame.com/images/cardlist/card/OP16-119.png")
    assert is_onepiece_official_image("https://onepiece-cardgame.com/images/cardlist/card/OP16-119.png")


def test_onepiece_official_image_rejects_spoofed_or_unrelated_hosts():
    assert not is_onepiece_official_image("https://www.onepiece-cardgame.com.evil.example/OP16-119.png")
    assert not is_onepiece_official_image("https://example.com/en.onepiece-cardgame.com/OP16-119.png")
    assert not is_onepiece_official_image("")
    assert not is_onepiece_official_image(None)
