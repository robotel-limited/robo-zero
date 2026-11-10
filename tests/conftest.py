"""Conftest comun: încarcă rule_effect_store (tabelul rule_exposures +
funcțiile expuse și pe project_rules_store) înaintea oricărui test, astfel
încât ambele căi de import să meargă în orice rulare izolată."""
import rule_effect_store  # noqa: F401
