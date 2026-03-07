# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy.orm import Session

from zvt.contract.schema import db_session_scope
from zvt.apps.stockpool.stockpool_schemas import StockPoolInfo, StockPools
from zvt.apps.stockpool.stockpool_utils import get_stock_pool_names
from zvt.apps.stockpool.stockpool_models import CreateStockPoolInfoModel, CreateStockPoolsModel
from zvt.apps.stockpool.common import StockPoolType
from zvt.apps.tag.common import InsertMode
from zvt.utils.time_utils import to_pd_timestamp, to_date_time_str, current_date

APPS_DB_PROVIDER = "zvt"


def _with_app_session(session: Optional[Session], data_schema, fn):
    if session is not None:
        return fn(session)
    with db_session_scope(provider=APPS_DB_PROVIDER, data_schema=data_schema) as sess:
        return fn(sess)


def build_stock_pool_info(
    create_stock_pool_info_model: CreateStockPoolInfoModel, timestamp, session: Optional[Session] = None
):
    def _do(sess: Session):
        stock_pool_info = StockPoolInfo(
            entity_id="admin",
            timestamp=to_pd_timestamp(timestamp),
            id=f"admin_{create_stock_pool_info_model.stock_pool_name}",
            stock_pool_type=create_stock_pool_info_model.stock_pool_type.value,
            stock_pool_name=create_stock_pool_info_model.stock_pool_name,
        )
        sess.add(stock_pool_info)
        sess.flush()
        return stock_pool_info

    return _with_app_session(session, StockPoolInfo, _do)


def build_stock_pool(
    create_stock_pools_model: CreateStockPoolsModel, target_date=current_date(), session: Optional[Session] = None
):
    def _do(sess: Session):
        entity_type = create_stock_pools_model.entity_type
        stock_pool_name = create_stock_pools_model.stock_pool_name

        if stock_pool_name not in get_stock_pool_names():
            build_stock_pool_info(
                CreateStockPoolInfoModel(stock_pool_type=StockPoolType.custom, stock_pool_name=stock_pool_name),
                timestamp=target_date,
                session=sess,
            )
        stock_pool_id = f"{entity_type}_{stock_pool_name}_{to_date_time_str(target_date)}"
        datas: List[StockPools] = StockPools.query_data(
            session=sess,
            filters=[StockPools.id == stock_pool_id],
            return_type="domain",
        )
        if datas:
            stock_pool = datas[0]
            if create_stock_pools_model.insert_mode == InsertMode.overwrite:
                stock_pool.entity_ids = create_stock_pools_model.entity_ids
            else:
                stock_pool.entity_ids = list(set(stock_pool.entity_ids + create_stock_pools_model.entity_ids))
        else:
            stock_pool = StockPools(
                entity_id=f"{entity_type}_{stock_pool_name}",
                timestamp=to_pd_timestamp(target_date),
                id=stock_pool_id,
                entity_type=entity_type,
                stock_pool_name=stock_pool_name,
                entity_ids=create_stock_pools_model.entity_ids,
            )
        sess.add(stock_pool)
        sess.flush()
        return stock_pool

    return _with_app_session(session, StockPools, _do)


def delete_stock_pool(stock_pool_name: str, session: Optional[Session] = None):
    def _do(sess: Session):
        stock_pool_info: List = StockPoolInfo.query_data(
            session=sess,
            filters=[StockPoolInfo.stock_pool_name == stock_pool_name],
            return_type="domain",
        )
        StockPools.del_data(filters=[StockPools.stock_pool_name == stock_pool_name])
        if stock_pool_info:
            sess.delete(stock_pool_info[0])
            return "success"
        return "not found"

    return _with_app_session(session, StockPoolInfo, _do)
