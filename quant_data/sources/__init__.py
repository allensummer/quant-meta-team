"""Source adapters (Layer 1).

Imports are deferred to avoid circular import with ``rate_limit`` (the tushare
adapter needs TokenBucket, which itself references DataSource / RateLimit).
"""
__all__ = ["TushareAdapter", "AkshareAdapter", "TemplateAdapter"]


def __getattr__(name):
    """PEP 562 lazy attribute access for circular-import safety."""
    if name == "TushareAdapter":
        from quant_data.sources.tushare import TushareAdapter as _T
        return _T
    if name == "AkshareAdapter":
        from quant_data.sources.akshare import AkshareAdapter as _A
        return _A
    if name == "TemplateAdapter":
        from quant_data.sources._template import TemplateAdapter as _P
        return _P
    raise AttributeError(name)
