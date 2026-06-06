from .base import BaseAgent
from .news_analysis import NewsAnalysisAgent
from .portfolio_monitoring import PortfolioMonitoringAgent
from .repair import RepairAgent
from .signal_ranking import SignalRankingAgent
from .watchlist_expansion import WatchlistExpansionAgent

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    NewsAnalysisAgent.name: NewsAnalysisAgent,
    SignalRankingAgent.name: SignalRankingAgent,
    WatchlistExpansionAgent.name: WatchlistExpansionAgent,
    PortfolioMonitoringAgent.name: PortfolioMonitoringAgent,
    RepairAgent.name: RepairAgent,
}

EVENT_AGENT_MAP: dict[str, str] = {
    "news_ingested": NewsAnalysisAgent.name,
    "analysis_completed": SignalRankingAgent.name,
    "high_priority_signal": PortfolioMonitoringAgent.name,
    "watchlist_candidate": WatchlistExpansionAgent.name,
    "fetch_failed": RepairAgent.name,
    "repair_required": RepairAgent.name,
    "portfolio_check": PortfolioMonitoringAgent.name,
}

__all__ = [
    "AGENT_REGISTRY",
    "EVENT_AGENT_MAP",
    "BaseAgent",
    "NewsAnalysisAgent",
    "SignalRankingAgent",
    "WatchlistExpansionAgent",
    "PortfolioMonitoringAgent",
    "RepairAgent",
]
