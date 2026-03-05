# -*- coding: utf-8 -*-
import logging
from typing import List, Union, Type

import pandas as pd
from sqlalchemy.ext.declarative import DeclarativeMeta
from sqlalchemy.orm import Session

from zvt.contract import zvt_context
from zvt.contract.schema import TradableEntity
from zvt.utils.pd_utils import pd_is_not_null


def get_entity_schema(entity_type: str) -> Type[TradableEntity]:
    """
    get entity schema from name

    :param entity_type: entity type, e.g. stock, stockus.
    :return: the Schema of the entity
    """
    return zvt_context.tradable_schema_map[entity_type]


def decode_entity_id(entity_id: str):
    """
    decode entity id to entity_type, exchange, code

    :param entity_id:
    :return: tuple with format (entity_type, exchange, code)
    """
    result = entity_id.split("_")
    entity_type = result[0]
    exchange = result[1]
    code = "".join(result[2:])
    return entity_type, exchange, code

def get_entity_exchange(entity_id: str):
    """
    get exchange by entity id

    :param entity_id:
    :return: exchange
    """
    _, exchange, _ = decode_entity_id(entity_id)
    return exchange


def get_entity_code(entity_id: str):
    """
    get code by entity id

    :param entity_id:
    :return: code
    """
    _, _, code = decode_entity_id(entity_id)
    return code


def get_entities(
    entity_schema: Type[TradableEntity] = None,
    entity_type: str = None,
    exchanges: List[str] = None,
    ids: List[str] = None,
    entity_ids: List[str] = None,
    entity_id: str = None,
    codes: List[str] = None,
    code: str = None,
    provider: str = None,
    columns: List = None,
    return_type: str = "df",
    start_timestamp: Union[pd.Timestamp, str] = None,
    end_timestamp: Union[pd.Timestamp, str] = None,
    filters: List = None,
    session: Session = None,
    order=None,
    limit: int = None,
    index: Union[str, list] = "code",
) -> List:
    """
    get entities by the arguments

    :param entity_schema:
    :param entity_type:
    :param exchanges:
    :param ids:
    :param entity_ids:
    :param entity_id:
    :param codes:
    :param code:
    :param provider:
    :param columns:
    :param return_type:
    :param start_timestamp:
    :param end_timestamp:
    :param filters:
    :param session:
    :param order:
    :param limit:
    :param index:
    :return:
    """
    if not entity_schema:
        entity_schema = zvt_context.tradable_schema_map[entity_type]

    if not provider:
        provider = entity_schema.get_providers()[0]

    if not order:
        order = entity_schema.code.asc()

    if exchanges:
        if filters:
            filters.append(entity_schema.exchange.in_(exchanges))
        else:
            filters = [entity_schema.exchange.in_(exchanges)]

    return entity_schema.query_data(
        ids=ids,
        entity_ids=entity_ids,
        entity_id=entity_id,
        codes=codes,
        code=code,
        level=None,
        provider=provider,
        columns=columns,
        return_type=return_type,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        filters=filters,
        session=session,
        order=order,
        limit=limit,
        index=index,
    )


def get_entity_ids(
    entity_type="stock",
    entity_schema: TradableEntity = None,
    exchanges=None,
    codes=None,
    provider=None,
    filters=None,
    entity_ids=None,
):
    """
    get entity ids by the arguments

    :param entity_type:
    :param entity_schema:
    :param exchanges:
    :param codes:
    :param provider:
    :param filters:
    :param entity_ids:
    :return:
    """
    df = get_entities(
        entity_type=entity_type,
        entity_schema=entity_schema,
        exchanges=exchanges,
        codes=codes,
        provider=provider,
        filters=filters,
        entity_ids=entity_ids,
    )
    if pd_is_not_null(df):
        return df["entity_id"].to_list()
    return None


if __name__ == "__main__":
    print(get_entities(entity_type="block"))


# the __all__ is generated
__all__ = [
    "get_entity_schema",
    "decode_entity_id",
    "get_entity_exchange",
    "get_entity_code",
    "get_entities",
    "get_entity_ids",
]
