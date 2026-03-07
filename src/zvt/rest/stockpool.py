# -*- coding: utf-8 -*-
"""REST API for stock pool (股票池)."""
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import zvt.apps.stockpool.stockpool_service as stockpool_service
import zvt.apps.tag.tag_service as tag_service
from zvt.apps.deps import get_app_db_session
from zvt.apps.stockpool.stockpool_models import (
    CreateStockPoolInfoModel,
    StockPoolInfoModel,
    CreateStockPoolsModel,
    StockPoolsModel,
)
from zvt.apps.stockpool.stockpool_schemas import StockPoolInfo, StockPools
from zvt.utils.time_utils import current_date

stockpool_router = APIRouter(
    prefix="/api/stockpool",
    tags=["stockpool"],
    responses={404: {"description": "Not found"}},
)


@stockpool_router.post("/create_stock_pool_info", response_model=StockPoolInfoModel)
def create_stock_pool_info(
    create_stock_pool_info_model: CreateStockPoolInfoModel,
    session: Session = Depends(get_app_db_session),
):
    return stockpool_service.build_stock_pool_info(
        create_stock_pool_info_model, timestamp=current_date(), session=session
    )


@stockpool_router.get("/get_stock_pool_info", response_model=List[StockPoolInfoModel])
def get_stock_pool_info(session: Session = Depends(get_app_db_session)):
    stock_pool_info: List[dict] = StockPoolInfo.query_data(session=session, return_type="dict")
    return [StockPoolInfoModel(**item) for item in stock_pool_info]


@stockpool_router.post("/create_stock_pools", response_model=StockPoolsModel)
def create_stock_pools(
    create_stock_pools_model: CreateStockPoolsModel,
    session: Session = Depends(get_app_db_session),
):
    return stockpool_service.build_stock_pool(
        create_stock_pools_model, current_date(), session=session
    )


@stockpool_router.delete("/delete_stock_pool", response_model=str)
def delete_stock_pool(stock_pool_name: str, session: Session = Depends(get_app_db_session)):
    return stockpool_service.delete_stock_pool(stock_pool_name=stock_pool_name, session=session)


@stockpool_router.get("/get_stock_pools", response_model=Optional[StockPoolsModel])
def get_stock_pools(stock_pool_name: str, session: Session = Depends(get_app_db_session)):
    stock_pools: List[dict] = StockPools.query_data(
        session=session,
        filters=[StockPools.stock_pool_name == stock_pool_name],
        order=StockPools.timestamp.desc(),
        limit=1,
        return_type="dict",
    )
    if stock_pools:
        return StockPoolsModel(**stock_pools[0])
    return None


@stockpool_router.get("/get_main_tags_in_stock_pool", response_model=List[str])
def get_main_tags_in_stock_pool(
    stock_pool_name: str, session: Session = Depends(get_app_db_session)
):
    return tag_service.get_main_tags_in_stock_pool(stock_pool_name, session=session)
