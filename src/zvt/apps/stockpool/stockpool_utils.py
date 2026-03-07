# -*- coding: utf-8 -*-
from typing import List

import pandas as pd

from zvt.utils.time_utils import now_pd_timestamp

from zvt.apps.stockpool.common import StockPoolType
from zvt.apps.stockpool.stockpool_schemas import StockPoolInfo


def _get_initial_stock_pool_info() -> List[dict]:
    timestamp = now_pd_timestamp()
    entity_id = "admin"
    return [
        {
            "id": f"{entity_id}_{stock_pool_name}",
            "entity_id": entity_id,
            "timestamp": timestamp,
            "stock_pool_type": StockPoolType.system.value,
            "stock_pool_name": stock_pool_name,
        }
        for stock_pool_name in ["主线", "年线", "大局", "A股", "美股主线", "港股主线"]
    ]


def build_initial_stock_pool_info(force_update=False):
    stock_pool_info_list = _get_initial_stock_pool_info()
    df = pd.DataFrame.from_records(stock_pool_info_list)
    StockPoolInfo.df_to_db(df, provider="zvt", force_update=force_update)


def get_stock_pool_names() -> List[str]:
    df = StockPoolInfo.query_data(columns=[StockPoolInfo.stock_pool_name])
    return df["stock_pool_name"].tolist()


__all__ = ["build_initial_stock_pool_info", "get_stock_pool_names"]
