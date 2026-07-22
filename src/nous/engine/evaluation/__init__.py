"""
评估模块: 荐股准确率/主线预测/绩效归因
"""
from .recommendation_metrics import (
    compute_recommendation_metrics,
    evaluate_from_screen_results,
    evaluate_from_model,
    save_evaluation_report,
)

from .theme_accuracy import (
    evaluate_theme_accuracy,
    evaluate_from_log,
    save_theme_report,
)

from .performance_attribution import (
    compute_performance_metrics,
    compute_performance_from_db,
    brinson_attribution,
    save_performance_report,
)

__all__ = [
    "compute_recommendation_metrics", "evaluate_from_screen_results", "evaluate_from_model", "save_evaluation_report",
    "evaluate_theme_accuracy", "evaluate_from_log", "save_theme_report",
    "compute_performance_metrics", "compute_performance_from_db", "brinson_attribution", "save_performance_report",
]
