# -*- coding: utf-8 -*-
import contextlib
import logging
from typing import List, Union, Type, Generator

import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import DeclarativeMeta
from sqlalchemy.orm import sessionmaker, Session

from zvt import zvt_env
from zvt.contract import zvt_context
from zvt.contract.register import ensure_schema_tables_and_indexes
from zvt.contract.route_registry import get_route_registry
from zvt.contract.schema import (
    TradableEntity,
    get_schema_columns,
    _ensure_schema_providers_loaded,
    get_schema_by_name,
)
from zvt.contract.storage import get_storage_backend

_initialized_storage_ids = set()
# Cache for bound sessionmaker by storage_id. Avoids re-binding on every call.
_bound_session_factories: dict = {}


def _storage_backend():
    return zvt_context.storage_backend or get_storage_backend()


def _route_registry():
    return zvt_context.route_registry or get_route_registry()
from zvt.utils.pd_utils import pd_is_not_null

logger = logging.getLogger(__name__)


def _get_db_name(data_schema: DeclarativeMeta) -> str:
    """
    get db name of the domain schema

    :param data_schema: the data schema
    :return: db name
    """
    for db_name, base in zvt_context.dbname_map_base.items():
        if issubclass(data_schema, base):
            return db_name


def get_db_engine(
    provider: str, db_name: str = None, data_schema: object = None, data_path: str = None
) -> Engine:
    """
    get db engine from (provider,db_name) or (provider,data_schema).
    Creates tables and indexes on first use (lazy init).
    """
    if data_schema:
        db_name = _get_db_name(data_schema=data_schema)

    if data_path is None:
        data_path = zvt_env.get("data_path", ".")

    storage_id = _route_registry().get_storage_id(provider, db_name)
    engine = _storage_backend().get_engine(storage_id, data_path)

    if storage_id not in _initialized_storage_ids:
        schema_base = zvt_context.dbname_map_base.get(db_name)
        if schema_base:
            ensure_schema_tables_and_indexes(engine, schema_base, db_name)
        _initialized_storage_ids.add(storage_id)

    return engine


def get_providers() -> List[str]:
    _ensure_schema_providers_loaded()
    return zvt_context.providers


def get_schemas(provider: str) -> List[DeclarativeMeta]:
    _ensure_schema_providers_loaded()
    schemas = []
    for provider1, dbs in zvt_context.provider_map_dbnames.items():
        if provider == provider1:
            for dbname in dbs:
                schemas1 = zvt_context.dbname_map_schemas.get(dbname)
                if schemas1:
                    schemas += schemas1
    return schemas


def _get_bound_session_factory(
    provider: str, db_name: str = None, data_schema: object = None
):
    """
    Get or create a sessionmaker bound to engine, cached by storage_id.
    """
    _ensure_schema_providers_loaded()
    if data_schema:
        db_name = _get_db_name(data_schema=data_schema)
    storage_id = _route_registry().get_storage_id(provider, db_name)
    if storage_id not in _bound_session_factories:
        engine = get_db_engine(provider=provider, db_name=db_name)
        _bound_session_factories[storage_id] = sessionmaker(
            bind=engine, autocommit=False, autoflush=True
        )
    return _bound_session_factories[storage_id]


def get_db_session(
    provider: str, db_name: str = None, data_schema: object = None
) -> Session:
    """
    Get a new db session for (provider, db_name) or (provider, data_schema).
    Caller is responsible for closing the session when done.
    Use db_session_scope() for automatic commit/rollback/close.

    :param provider: data provider
    :param db_name: db name
    :param data_schema: data schema (used to resolve db_name if db_name is None)
    :return: new Session instance
    """
    session_fac = _get_bound_session_factory(
        provider=provider, db_name=db_name, data_schema=data_schema
    )
    return session_fac()


@contextlib.contextmanager
def db_session_scope(
    provider: str, db_name: str = None, data_schema: object = None
) -> Generator[Session, None, None]:
    """
    Context manager for db session. Commits on success, rolls back on exception,
    and always closes the session.

    Usage:
        with db_session_scope(provider="zvt", data_schema=StockTags) as session:
            session.query(StockTags).filter(...).all()
    """
    session = get_db_session(provider=provider, db_name=db_name, data_schema=data_schema)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session_factory(provider: str, db_name: str = None, data_schema: object = None):
    """
    Get bound session factory from (provider, db_name) or (provider, data_schema).
    Returns sessionmaker; call it to create a new session.
    """
    return _get_bound_session_factory(
        provider=provider, db_name=db_name, data_schema=data_schema
    )


def close_all_sessions() -> None:
    """
    Close all sessions stored in zvt_context.sessions.
    Call before process exit or in tests to release connections.
    """
    for key, session in list(zvt_context.sessions.items()):
        try:
            session.close()
        except Exception:
            logger.exception("Error closing session %s", key)
        finally:
            del zvt_context.sessions[key]


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


def get_entity_type(entity_id: str):
    """
    get entity type by entity id

    :param entity_id:
    :return: entity type
    """
    entity_type, _, _ = decode_entity_id(entity_id)
    return entity_type


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
    "get_db_engine",
    "get_providers",
    "get_schemas",
    "get_db_session",
    "get_db_session_factory",
    "db_session_scope",
    "close_all_sessions",
    "get_entity_schema",
    "get_schema_by_name",
    "get_schema_columns",
    "decode_entity_id",
    "get_entity_type",
    "get_entity_exchange",
    "get_entity_code",
    "get_entities",
    "get_entity_ids",
]
