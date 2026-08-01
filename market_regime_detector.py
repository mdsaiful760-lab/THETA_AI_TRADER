
"""
THETA AI TRADER
market_regime_detector.py
Version: 1.0.0
Sprint: 09.2

Production foundation for Market Regime Detection.
Analytical only.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict


class MarketRegime(str, Enum):
    RANGE_BOUND="RANGE_BOUND"
    BULLISH="BULLISH"
    BEARISH="BEARISH"
    VOLATILE="VOLATILE"
    BREAKOUT="BREAKOUT"
    BREAKDOWN="BREAKDOWN"
    UNKNOWN="UNKNOWN"


@dataclass(frozen=True)
class MarketSnapshot:
    trend:str
    atm_iv:float
    iv_regime:str
    expected_move:float
    market_quality:float
    pcr:float
    liquidity:str="GOOD"
    gamma:str="LOW"


@dataclass(frozen=True)
class AnalyzerScore:
    score:float
    reason:str


@dataclass(frozen=True)
class RegimeResult:
    regime:MarketRegime
    confidence:float
    diagnostics:Dict[str,AnalyzerScore]


class MarketRegimeDetector:

    def _trend(self,s):
        t=s.trend.upper()
        if t=="NEUTRAL":
            return AnalyzerScore(95,"Neutral trend")
        if "BULL" in t:
            return AnalyzerScore(85,"Bullish trend")
        if "BEAR" in t:
            return AnalyzerScore(85,"Bearish trend")
        return AnalyzerScore(60,"Unknown trend")

    def _iv(self,s):
        iv=s.iv_regime.upper()
        m={"VERY_HIGH":98,"HIGH":92,"NORMAL":70,"LOW":40}
        return AnalyzerScore(m.get(iv,50),f"IV regime={iv}")

    def _pcr(self,s):
        if 0.9<=s.pcr<=1.1:
            return AnalyzerScore(90,"Balanced PCR")
        if s.pcr>1.1:
            return AnalyzerScore(75,"Bullish PCR")
        return AnalyzerScore(75,"Bearish PCR")

    def _liq(self,s):
        m={"EXCELLENT":100,"GOOD":90,"AVERAGE":70,"POOR":40}
        return AnalyzerScore(m.get(s.liquidity.upper(),60),"Liquidity")

    def _quality(self,s):
        return AnalyzerScore(max(0,min(100,s.market_quality)),"Market quality")

    def detect(self,s:MarketSnapshot)->RegimeResult:
        d={
            "trend":self._trend(s),
            "iv":self._iv(s),
            "pcr":self._pcr(s),
            "liquidity":self._liq(s),
            "quality":self._quality(s)
        }
        avg=sum(x.score for x in d.values())/len(d)

        if s.trend.upper()=="NEUTRAL" and s.iv_regime.upper() in ("HIGH","VERY_HIGH"):
            regime=MarketRegime.RANGE_BOUND
        elif "BULL" in s.trend.upper():
            regime=MarketRegime.BULLISH
        elif "BEAR" in s.trend.upper():
            regime=MarketRegime.BEARISH
        elif s.iv_regime.upper()=="VERY_HIGH":
            regime=MarketRegime.VOLATILE
        else:
            regime=MarketRegime.UNKNOWN

        return RegimeResult(regime,round(avg,2),d)


if __name__=="__main__":
    detector=MarketRegimeDetector()
    snapshot=MarketSnapshot(
        trend="NEUTRAL",
        atm_iv=13.2,
        iv_regime="HIGH",
        expected_move=155,
        market_quality=94,
        pcr=1.02,
        liquidity="EXCELLENT"
    )
    result=detector.detect(snapshot)

    print("="*72)
    print("THETA AI TRADER - MARKET REGIME DETECTOR v1.0")
    print("="*72)
    print(f"Regime      : {result.regime.value}")
    print(f"Confidence  : {result.confidence}%")
    print("-"*72)
    for name,score in result.diagnostics.items():
        print(f"{name:12} {score.score:6.1f}   {score.reason}")
