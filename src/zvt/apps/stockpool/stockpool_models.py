# -*- coding: utf-8 -*-
from typing import List

from pydantic import field_validator

from zvt.contract.model import MixinModel, CustomModel
from zvt.apps.tag.common import InsertMode
from zvt.apps.stockpool.common import StockPoolType
from zvt.apps.stockpool.stockpool_utils import get_stock_pool_names


class StockPoolModel(MixinModel):
    stock_pool_name: str
    entity_ids: List[str]


class StockPoolInfoModel(MixinModel):
    stock_pool_type: StockPoolType
    stock_pool_name: str


class CreateStockPoolInfoModel(CustomModel):
    stock_pool_type: StockPoolType
    stock_pool_name: str

    @field_validator("stock_pool_name")
    @classmethod
    def stock_pool_name_not_existed(cls, v: str) -> str:
        if v in get_stock_pool_names():
            raise ValueError(f"stock_pool_name: {v} has been used")
        return v


class StockPoolsModel(MixinModel):
    stock_pool_name: str
    entity_ids: List[str]


class CreateStockPoolsModel(CustomModel):
    entity_type: str = "stock"
    stock_pool_name: str
    entity_ids: List[str]
    insert_mode: InsertMode = InsertMode.overwrite


__all__ = [
    "StockPoolModel",
    "StockPoolInfoModel",
    "CreateStockPoolInfoModel",
    "StockPoolsModel",
    "CreateStockPoolsModel",
]
