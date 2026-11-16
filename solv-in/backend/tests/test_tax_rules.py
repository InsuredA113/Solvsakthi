from datetime import date
from app.tax_rules import rule_is_applicable, rule_specificity

def base_rule(**overrides):
    rule = {
        "country_code": "US", "currency": "USD", "admin_area": "California",
        "region": "", "locality": "", "tax_name": "Sales tax", "status": "Active",
        "effective_from": "2026-01-01", "effective_to": "2026-12-31"
    }
    rule.update(overrides)
    return rule

def test_rule_matches_country_admin_area_currency_and_effective_date():
    assert rule_is_applicable(base_rule(), country_code="US", currency="USD",
        admin_area="California", region="", locality="", tax_point_date=date(2026, 6, 1))

def test_rule_does_not_match_outside_effective_dates():
    assert not rule_is_applicable(base_rule(), country_code="US", currency="USD",
        admin_area="California", region="", locality="", tax_point_date=date(2027, 1, 1))

def test_inactive_rule_never_matches():
    assert not rule_is_applicable(base_rule(status="Inactive"), country_code="US", currency="USD",
        admin_area="California", region="", locality="", tax_point_date=date(2026, 6, 1))

def test_jurisdiction_specificity_prefers_local_rule():
    assert rule_specificity(base_rule(locality="Los Angeles")) > rule_specificity(base_rule())


def test_select_applicable_rules_prefers_more_specific_jurisdiction():
    from app.tax_rules import select_applicable_tax_rules
    broad = base_rule(admin_area="", effective_from="2026-01-01", effective_to=None)
    local = base_rule(locality="Los Angeles")
    selected, ambiguous = select_applicable_tax_rules(
        [broad, local], country_code="US", currency="USD", admin_area="California",
        region="", locality="Los Angeles", tax_point_date=date(2026, 6, 1)
    )
    assert selected == [local]
    assert ambiguous == []

def test_select_applicable_rules_flags_same_specificity_duplicates():
    from app.tax_rules import select_applicable_tax_rules
    first = base_rule()
    second = base_rule(authority_reference="Different official schedule")
    selected, ambiguous = select_applicable_tax_rules(
        [first, second], country_code="US", currency="USD", admin_area="California",
        region="", locality="", tax_point_date=date(2026, 6, 1)
    )
    assert selected == []
    assert ambiguous == ["Sales tax"]
