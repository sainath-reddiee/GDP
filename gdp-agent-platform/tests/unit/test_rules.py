from services.common.rules import DEFAULTS, ends_with_hint, load_rules, merged, rule, using


def test_defaults_match_the_platform_behaviour():
    assert rule("quality.enum_max") == 20 and rule("profile.value_pii_share") == 0.8


def test_overrides_are_typed_and_unknown_keys_ignored():
    r = merged({"quality.enum_max": "35", "relationships.min_confidence": 0.7, "made.up": 1,
                "hints.freshness_columns": "not a list"})
    assert r["quality.enum_max"] == 35 and r["relationships.min_confidence"] == 0.7
    assert "made.up" not in r and r["hints.freshness_columns"] == DEFAULTS["hints.freshness_columns"]


def test_domain_rules_override_platform_rules():
    def query(sql, params):
        if "PLATFORM_CONFIG" in sql:
            return [{"CONFIG_VALUE": '{"quality.enum_max": 30, "joins.ambiguity_margin": 0.2}'}]
        return [{"config": {"rules": {"quality.enum_max": 50}}}]  # API rows come back lower-case

    r = load_rules(query, "d1")
    assert r["quality.enum_max"] == 50 and r["joins.ambiguity_margin"] == 0.2


def test_broken_config_falls_back_to_defaults():
    def query(sql, params):
        raise RuntimeError("no access")

    assert load_rules(query, "d1") == DEFAULTS


def test_hints_are_data_for_another_company():
    assert not ends_with_hint("CUSTOMER_REFNO", "hints.identifier_suffixes")
    with using(merged({"hints.identifier_suffixes": ["_REFNO"]})):
        assert ends_with_hint("customer_refno", "hints.identifier_suffixes")
    assert not ends_with_hint("CUSTOMER_REFNO", "hints.identifier_suffixes")


def test_threshold_override_changes_a_rule_decision():
    from services.mapping import scoring

    cands = [{"scores": {"semantic": 0.9}, "target": {"column_name": "A"}, "evidence": {}},
             {"scores": {"semantic": 0.85}, "target": {"column_name": "B"}, "evidence": {}}]
    weights = {c: (1.0 if c == "semantic" else 0.0) for c in scoring.COMPONENTS}
    thresholds = {"auto_suggest": 0.5, "human_review": 0.3}
    loose = scoring.rank_candidates([dict(c) for c in cands], weights, thresholds, 2)
    with using(merged({"mapping.ambiguity_margin": 0.01})):
        strict = scoring.rank_candidates([dict(c) for c in cands], weights, thresholds, 2)
    assert loose[0]["ambiguous"] and not strict[0]["ambiguous"]
