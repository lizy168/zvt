# -*- coding: utf-8 -*-
# Table definitions for tag app; registered to zvt_apps in zvt.apps.__init__

from sqlalchemy import Column, String, JSON, Boolean, Float, Integer

from zvt.contract import Mixin
from zvt.apps.base import AppsBase


class IndustryInfo(AppsBase, Mixin):
    __tablename__ = "industry_info"

    industry_name = Column(String, unique=True)
    description = Column(String)
    # related main tag
    main_tag = Column(String)


class MainTagInfo(AppsBase, Mixin):
    __tablename__ = "main_tag_info"

    tag = Column(String, unique=True)
    tag_reason = Column(String)


class SubTagInfo(AppsBase, Mixin):
    __tablename__ = "sub_tag_info"

    tag = Column(String, unique=True)
    tag_reason = Column(String)

    # related main tag
    main_tag = Column(String)


class HiddenTagInfo(AppsBase, Mixin):
    __tablename__ = "hidden_tag_info"

    tag = Column(String, unique=True)
    tag_reason = Column(String)


class StockTags(AppsBase, Mixin):
    """
    Schema for storing stock tags
    """

    __tablename__ = "stock_tags"

    entity_type = Column(String(length=64))

    code = Column(String(length=64))
    name = Column(String(length=128))

    main_tag = Column(String)
    main_tag_reason = Column(String)
    main_tags = Column(JSON)

    sub_tag = Column(String)
    sub_tag_reason = Column(String)
    sub_tags = Column(JSON)

    active_hidden_tags = Column(JSON)
    hidden_tags = Column(JSON)
    set_by_user = Column(Boolean, default=False)


class StockSystemTags(AppsBase, Mixin):
    __tablename__ = "stock_system_tags"
    #: 编码
    code = Column(String(length=64))
    #: 名字
    name = Column(String(length=128))
    #: 减持
    recent_reduction = Column(Boolean)
    #: 增持
    recent_acquisition = Column(Boolean)
    #: 解禁
    recent_unlock = Column(Boolean)
    #: 增发配股
    recent_additional_or_rights_issue = Column(Boolean)
    #: 业绩利好
    recent_positive_earnings_news = Column(Boolean)
    #: 业绩利空
    recent_negative_earnings_news = Column(Boolean)
    #: 上榜次数
    recent_dragon_and_tiger_count = Column(Integer)
    #: 违规行为
    recent_violation_alert = Column(Boolean)
    #: 利好
    recent_positive_news = Column(Boolean)
    #: 利空
    recent_negative_news = Column(Boolean)
    #: 新闻总结
    recent_news_summary = Column(JSON)


class TagStats(AppsBase, Mixin):
    __tablename__ = "tag_stats"

    entity_type = Column(String(length=64))

    stock_pool_name = Column(String)
    main_tag = Column(String)
    turnover = Column(Float)
    entity_count = Column(Integer)
    position = Column(Integer)
    is_main_line = Column(Boolean)
    main_line_continuous_days = Column(Integer)
    entity_ids = Column(JSON)


# the __all__ is generated
__all__ = [
    "IndustryInfo",
    "MainTagInfo",
    "SubTagInfo",
    "HiddenTagInfo",
    "StockTags",
    "StockSystemTags",
    "TagStats",
]
