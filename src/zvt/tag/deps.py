# -*- coding: utf-8 -*-
"""
Tag 模块统一的 DB session 依赖注入。

所有 tag 相关表（stock_tags, main_tag_info, sub_tag_info 等）使用同一数据库，
通过 FastAPI Depends(get_tag_db_session) 在请求内复用同一 session，
由依赖在请求结束时统一 commit/rollback/close。
"""
from typing import Generator

from sqlalchemy.orm import Session

from zvt.contract.schema import get_db_session
from zvt.tag.tag_schemas import StockTags


def get_tag_db_session() -> Generator[Session, None, None]:
    """
    FastAPI 依赖：为当前请求提供 tag 库的 DB session。
    请求结束时自动 commit（成功）或 rollback（异常），并关闭 session。
    """
    session = get_db_session(provider="zvt", data_schema=StockTags)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
