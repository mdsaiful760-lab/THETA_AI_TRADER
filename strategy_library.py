
"""
THETA AI TRADER
strategy_library.py
Version: 0.2.0
Sprint: 08.2

Expanded Strategy Knowledge Base
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class StrategyCategory(str, Enum):
    INCOME = "Income"
    DIRECTIONAL = "Directional"
    VOLATILITY = "Volatility"
    ADVANCED = "Advanced"


class MarketRegime(str, Enum):
    RANGE_BOUND = "Range Bound"
    BULLISH = "Bullish"
    BEARISH = "Bearish"
    VOLATILE = "Volatile"
    BREAKOUT = "Breakout"
    BREAKDOWN = "Breakdown"


class RiskProfile(str, Enum):
    DEFINED = "Defined Risk"
    UNDEFINED = "Undefined Risk"


@dataclass(frozen=True)
class StrategyMarketProfile:
    preferred_regimes: List[MarketRegime]
    preferred_iv: str
    preferred_trend: str
    liquidity: str


@dataclass(frozen=True)
class StrategyGreeksProfile:
    theta: str
    delta: str
    gamma: str
    vega: str


@dataclass(frozen=True)
class StrategyExecutionProfile:
    legs: int
    adjustment_allowed: bool
    rolling_allowed: bool
    partial_exit_allowed: bool


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    category: StrategyCategory
    description: str
    risk_profile: RiskProfile
    market: StrategyMarketProfile
    greeks: StrategyGreeksProfile
    execution: StrategyExecutionProfile
    tags: List[str] = field(default_factory=list)


class StrategyRegistry:
    def __init__(self):
        self._items: Dict[str, StrategyDefinition] = {}

    def register(self, strategy: StrategyDefinition):
        if strategy.strategy_id in self._items:
            raise ValueError(f"Duplicate strategy: {strategy.strategy_id}")
        self._items[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> StrategyDefinition:
        return self._items[strategy_id]

    def all(self):
        return list(self._items.values())

    def by_category(self, category: StrategyCategory):
        return [s for s in self._items.values() if s.category == category]


registry = StrategyRegistry()


def add(
    sid, name, category, desc, regimes, risk,
    iv, trend, liquidity,
    theta, delta, gamma, vega,
    legs, tags
):
    registry.register(
        StrategyDefinition(
            strategy_id=sid,
            name=name,
            category=category,
            description=desc,
            risk_profile=risk,
            market=StrategyMarketProfile(regimes, iv, trend, liquidity),
            greeks=StrategyGreeksProfile(theta, delta, gamma, vega),
            execution=StrategyExecutionProfile(
                legs=legs,
                adjustment_allowed=True,
                rolling_allowed=True,
                partial_exit_allowed=True,
            ),
            tags=tags,
        )
    )


# Income
add("SHORT_STRANGLE","Short Strangle",StrategyCategory.INCOME,"Range premium selling",
    [MarketRegime.RANGE_BOUND],RiskProfile.UNDEFINED,
    "HIGH","NEUTRAL","EXCELLENT",
    "HIGH","LOW","LOW","NEGATIVE",2,["theta","premium"])

add("SHORT_STRADDLE","Short Straddle",StrategyCategory.INCOME,"ATM premium selling",
    [MarketRegime.RANGE_BOUND],RiskProfile.UNDEFINED,
    "HIGH","NEUTRAL","EXCELLENT",
    "HIGH","LOW","HIGH","NEGATIVE",2,["theta"])

add("IRON_CONDOR","Iron Condor",StrategyCategory.INCOME,"Defined risk range strategy",
    [MarketRegime.RANGE_BOUND],RiskProfile.DEFINED,
    "HIGH","NEUTRAL","GOOD",
    "HIGH","LOW","LOW","NEGATIVE",4,["defined"])

add("IRON_BUTTERFLY","Iron Butterfly",StrategyCategory.INCOME,"Defined risk ATM strategy",
    [MarketRegime.RANGE_BOUND],RiskProfile.DEFINED,
    "HIGH","NEUTRAL","GOOD",
    "HIGH","LOW","MEDIUM","NEGATIVE",4,[])

# Bullish
add("BULL_PUT_SPREAD","Bull Put Spread",StrategyCategory.DIRECTIONAL,"Bullish credit spread",
    [MarketRegime.BULLISH],RiskProfile.DEFINED,
    "MEDIUM","BULLISH","GOOD",
    "MEDIUM","POSITIVE","LOW","NEGATIVE",2,[])

add("BULL_CALL_SPREAD","Bull Call Spread",StrategyCategory.DIRECTIONAL,"Bullish debit spread",
    [MarketRegime.BULLISH],RiskProfile.DEFINED,
    "LOW","BULLISH","GOOD",
    "LOW","POSITIVE","LOW","POSITIVE",2,[])

add("LONG_CALL","Long Call",StrategyCategory.DIRECTIONAL,"Long bullish option",
    [MarketRegime.BREAKOUT],RiskProfile.DEFINED,
    "LOW","STRONG_BULL","GOOD",
    "LOW","POSITIVE","POSITIVE","POSITIVE",1,[])

# Bearish
add("BEAR_CALL_SPREAD","Bear Call Spread",StrategyCategory.DIRECTIONAL,"Bearish credit spread",
    [MarketRegime.BEARISH],RiskProfile.DEFINED,
    "MEDIUM","BEARISH","GOOD",
    "MEDIUM","NEGATIVE","LOW","NEGATIVE",2,[])

add("BEAR_PUT_SPREAD","Bear Put Spread",StrategyCategory.DIRECTIONAL,"Bearish debit spread",
    [MarketRegime.BEARISH],RiskProfile.DEFINED,
    "LOW","BEARISH","GOOD",
    "LOW","NEGATIVE","LOW","POSITIVE",2,[])

add("LONG_PUT","Long Put",StrategyCategory.DIRECTIONAL,"Long bearish option",
    [MarketRegime.BREAKDOWN],RiskProfile.DEFINED,
    "LOW","STRONG_BEAR","GOOD",
    "LOW","NEGATIVE","POSITIVE","POSITIVE",1,[])

# Volatility
add("LONG_STRADDLE","Long Straddle",StrategyCategory.VOLATILITY,"Volatility expansion",
    [MarketRegime.VOLATILE],RiskProfile.DEFINED,
    "LOW","ANY","GOOD",
    "LOW","NEUTRAL","HIGH","POSITIVE",2,[])

add("LONG_STRANGLE","Long Strangle",StrategyCategory.VOLATILITY,"Volatility expansion OTM",
    [MarketRegime.VOLATILE],RiskProfile.DEFINED,
    "LOW","ANY","GOOD",
    "LOW","NEUTRAL","MEDIUM","POSITIVE",2,[])

add("CALENDAR_SPREAD","Calendar Spread",StrategyCategory.VOLATILITY,"Time spread",
    [MarketRegime.RANGE_BOUND],RiskProfile.DEFINED,
    "MEDIUM","NEUTRAL","GOOD",
    "HIGH","LOW","LOW","POSITIVE",2,[])

# Advanced
add("JADE_LIZARD","Jade Lizard",StrategyCategory.ADVANCED,"Premium strategy",
    [MarketRegime.RANGE_BOUND],RiskProfile.UNDEFINED,
    "HIGH","NEUTRAL","GOOD",
    "HIGH","LOW","LOW","NEGATIVE",3,[])

add("BROKEN_WING_BUTTERFLY","Broken Wing Butterfly",StrategyCategory.ADVANCED,"Asymmetric butterfly",
    [MarketRegime.RANGE_BOUND],RiskProfile.DEFINED,
    "MEDIUM","NEUTRAL","GOOD",
    "MEDIUM","LOW","LOW","NEGATIVE",4,[])

if __name__=="__main__":
    print("Strategy Library v0.2")
    print(f"Total strategies: {len(registry.all())}")
    for s in registry.all():
        print(f"{s.strategy_id:24} | {s.category.value}")
