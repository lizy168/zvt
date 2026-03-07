# -*- coding: utf-8 -*-
# Table definitions for stockpool app; registered to zvt_apps in zvt.apps.__init__

from sqlalchemy import Column, String, JSON

from zvt.contract import Mixin
from zvt.apps.base import AppsBase


class StockPoolInfo(AppsBase, Mixin):
    __tablename__ = "stock_pool_info"
    stock_pool_type = Column(String)
    stock_pool_name = Column(String, unique=True)


class StockPools(AppsBase, Mixin):
    __tablename__ = "stock_pools"

    entity_type = Column(String(length=64))
    stock_pool_name = Column(String)
    entity_ids = Column(JSON)


__all__ = ["StockPoolInfo", "StockPools"]
