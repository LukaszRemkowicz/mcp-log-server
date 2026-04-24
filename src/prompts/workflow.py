"""Workflow prompt preparation helpers."""

from utils.assets import WorkflowAssetLoader


def build_daily_log_prompt(asset_loader: WorkflowAssetLoader) -> str:
    """Build the prepared daily log analysis prompt text."""

    return "\n\n".join(
        [
            asset_loader.load_text("prompts/monitoring_job_system.md").strip(),
            asset_loader.load_text("prompts/monitoring_job_rules.md").strip(),
            asset_loader.load_text("prompts/monitoring_log_summary.md").strip(),
            asset_loader.load_text("prompts/monitoring_log_response_format.md").strip(),
        ]
    )
