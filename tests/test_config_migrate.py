import config as cfg_mod


def test_migrate_drops_legacy_sections_and_renames_keys():
    user = {
        "whisper": {"model": "small"},
        "claude": {"model": "claude-haiku-4-5-20251001"},
        "dictionary": {"fuzzy_threshold": 85, "inject_into_whisper": False},
    }
    assert cfg_mod._migrate(user) is True
    assert "whisper" not in user and "claude" not in user
    assert user["dictionary"] == {"fuzzy_threshold": 85, "inject_into_cleanup": False}
    assert user["sarvam"]["stt_model"] == cfg_mod.DEFAULTS["sarvam"]["stt_model"]


def test_migrate_is_noop_on_current_config():
    user = {"sarvam": dict(cfg_mod.DEFAULTS["sarvam"]), "dictionary": {"inject_into_cleanup": True}}
    assert cfg_mod._migrate(user) is False
