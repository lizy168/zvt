# -*- coding: utf-8 -*-
"""
Application / business modules: tag, trading, etc.
All app data uses a single DB (zvt_apps); schemas are registered here.
"""
from zvt.apps.base import AppsBase
from zvt.contract.register import register_schema

# Import schema modules so table classes are bound to AppsBase
from zvt.apps.tag import tag_schemas  # noqa: F401
from zvt.apps.stockpool import stockpool_schemas  # noqa: F401
from zvt.apps.trading import trading_schemas  # noqa: F401

register_schema(db_name="zvt_apps", schema_base=AppsBase, internal=True)

__all__ = ["AppsBase"]
