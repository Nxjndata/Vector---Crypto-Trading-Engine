"""Prometheus metrics exposition endpoint."""

from fastapi import APIRouter, Response

from trading_platform.monitoring.metrics import METRICS

router = APIRouter(tags=["Metrics"])


@router.get("/metrics")
async def get_metrics() -> Response:
    """Expose Prometheus formatted metrics for scrapers."""
    content = METRICS.generate_prometheus_text()
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")
