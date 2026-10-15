"""Frozen analysis plan, shared unchanged with preregistration.

The contract lives in cards (layer zero) so prereg (layer one) need not import
analysis (layer two). Analysis must reload this object, never rebuild a plan
from a newer registry when executing an archived preregistration.
"""

from qr_llm_evals.cards.statistical_plan import StatisticalPlan as StatisticalPlan
from qr_llm_evals.cards.statistical_plan import plan_hash as plan_hash
