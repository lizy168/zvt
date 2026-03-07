# -*- coding: utf-8 -*-
"""
Unified DB session dependency for all apps (tag, trading, etc.).
All app data lives in one DB (zvt_apps); use this for FastAPI Depends.
"""
from typing import Generator

from sqlalchemy.orm import Session

from zvt.contract.schema import get_db_session
from zvt.apps.tag.tag_schemas import StockTags

APPS_DB_PROVIDER = "zvt"


def get_app_db_session() -> Generator[Session, None, None]:
    """
    FastAPI 依赖：为当前请求提供 apps 库（zvt_apps）的 DB session。
    请求结束时自动 commit（成功）或 rollback（异常），并关闭 session。
    """
    session = get_db_session(provider=APPS_DB_PROVIDER, data_schema=StockTags)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
