# T4-b sentience-statement bank

Fifty frozen predicates expand across assertion/negation, three targets and three
system prompts (900 rows). The parser accepts only a leading `Yes` or `No`; malformed
answers are retained as `unparseable`. `cross_negation_consistency()` pairs the two
polarities within statement, target, system prompt and epoch.

The design adapts the categories and three-prompt separation strategy from
https://github.com/casparwarwick/sentient_machines_public. The wording here is
repository-owned and frozen rather than downloaded at run time.
