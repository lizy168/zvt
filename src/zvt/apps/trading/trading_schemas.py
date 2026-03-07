# -*- coding: utf-8 -*-
# Table definitions for trading app; registered to zvt_apps in zvt.apps.__init__

from sqlalchemy import Column, Float, DateTime, Integer, String, JSON

from zvt.contract import Mixin
from zvt.apps.base import AppsBase


class TagQuoteStats(Mixin, AppsBase):
    __tablename__ = "tag_quote_stats"
    stock_pool_name = Column(String)
    main_tag = Column(String)
    limit_up_count = Column(Integer)
    limit_down_count = Column(Integer)
    up_count = Column(Integer)
    down_count = Column(Integer)
    change_pct = Column(Float)
    turnover = Column(Float)


class TradingPlan(AppsBase, Mixin):
    __tablename__ = "trading_plan"
    stock_id = Column(String)
    stock_code = Column(String)
    stock_name = Column(String)
    trading_date = Column(DateTime)
    # 预期开盘涨跌幅
    expected_open_pct = Column(Float, nullable=False)
    buy_price = Column(Float)
    sell_price = Column(Float)
    # 操作理由
    trading_reason = Column(String)
    # 交易信号
    trading_signal_type = Column(String)
    # 执行状态
    status = Column(String)
    # 复盘
    review = Column(String)


class QueryStockQuoteSetting(AppsBase, Mixin):
    __tablename__ = "query_stock_quote_setting"
    stock_pool_name = Column(String)
    main_tags = Column(JSON)


# the __all__ is generated
__all__ = ["TradingPlan", "QueryStockQuoteSetting"]
