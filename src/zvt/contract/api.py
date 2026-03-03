# -*- coding: utf-8 -*-
import contextlib
import logging
import platform
from typing import List, Union, Type, Generator

import pandas as pd
from sqlalchemy import func, exists, and_
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import DeclarativeMeta
from sqlalchemy.orm import Query
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql.expression import text

from zvt import zvt_env
from zvt.contract import IntervalLevel
from zvt.contract import zvt_context
from zvt.contract.schema import Mixin, TradableEntity
from zvt.contract.storage import get_storage_backend
from zvt.contract.route_registry import get_route_registry
from zvt.contract.register import ensure_schema_tables_and_indexes

_initialized_storage_ids = set()
# Cache for bound sessionmaker by storage_id. Avoids re-binding on every call.
_bound_session_factories: dict = {}


def _storage_backend():
    return zvt_context.storage_backend or get_storage_backend()


def _route_registry():
    return zvt_context.route_registry or get_route_registry()
from zvt.utils.pd_utils import pd_is_not_null, index_df
from zvt.utils.time_utils import to_pd_timestamp

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


def _ensure_schema_providers_loaded():
    """Populate zvt_context from config schema_providers for schemas without Recorders."""
    if getattr(_ensure_schema_providers_loaded, "_done", False):
        return
    try:
        from zvt.contract.schema import _get_schema_providers
        schema_providers = _get_schema_providers()
        for db_name, providers in schema_providers.items():
            for p in providers:
                if p not in zvt_context.providers:
                    zvt_context.providers.append(p)
                if p not in zvt_context.provider_map_dbnames:
                    zvt_context.provider_map_dbnames[p] = []
                if db_name not in zvt_context.provider_map_dbnames[p]:
                    zvt_context.provider_map_dbnames[p].append(db_name)
    except Exception:
        pass
    _ensure_schema_providers_loaded._done = True


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


DBSession = get_db_session_factory


def get_entity_schema(entity_type: str) -> Type[TradableEntity]:
    """
    get entity schema from name

    :param entity_type: entity type, e.g. stock, stockus.
    :return: the Schema of the entity
    """
    return zvt_context.tradable_schema_map[entity_type]


def get_schema_by_name(name: str) -> DeclarativeMeta:
    """
    get domain schema by the name

    :param name: schema name
    :return: schema
    """
    for schema in zvt_context.schemas:
        if schema.__name__ == name:
            return schema


def get_schema_columns(schema: DeclarativeMeta) -> List[str]:
    """
    get all columns of the domain schema

    :param schema: data schema
    :return: columns of the schema
    """
    return schema.__table__.columns.keys()


def common_filter(
    query: Query,
    data_schema,
    start_timestamp=None,
    end_timestamp=None,
    filters=None,
    order=None,
    limit=None,
    distinct=None,
    time_field="timestamp",
):
    """
    build filter by the arguments

    :param query: sql query
    :param data_schema: data schema
    :param start_timestamp: start timestamp
    :param end_timestamp: end timestamp
    :param filters: sql filters
    :param order: sql order
    :param limit: sql limit size
    :param time_field: time field in columns
    :return: result query
    """
    assert data_schema is not None
    time_col = eval("data_schema.{}".format(time_field))

    if start_timestamp:
        query = query.filter(time_col >= to_pd_timestamp(start_timestamp))
    if end_timestamp:
        query = query.filter(time_col <= to_pd_timestamp(end_timestamp))

    if filters:
        for filter in filters:
            query = query.filter(filter)
    if order is not None:
        query = query.order_by(order)
    else:
        query = query.order_by(time_col.asc())
    if limit:
        query = query.limit(limit)
    if distinct:
        query = query.distinct(distinct)

    return query


def del_data(data_schema: Type[Mixin], filters: List = None, provider=None):
    """
    delete data by filters

    :param data_schema: data schema
    :param filters: filters
    :param provider: data provider
    """
    if not provider:
        provider = data_schema.get_providers()[0]

    with db_session_scope(provider=provider, data_schema=data_schema) as session:
        query = session.query(data_schema)
        if filters:
            for f in filters:
                query = query.filter(f)
        query.delete()


def get_by_id(session: Session, data_schema, id: str):
    """
    get one record by id from data schema.

    :param session: db session (required; use get_db_session() or db_session_scope())
    :param data_schema: data schema
    :param id: the record id
    """
    _ensure_schema_providers_loaded()
    return session.query(data_schema).get(id)


def _row2dict(row):
    d = {}
    for column in row.__table__.columns:
        d[column.name] = getattr(row, column.name)
    return d


def get_data(
    data_schema: Type[Mixin],
    ids: List[str] = None,
    entity_ids: List[str] = None,
    entity_id: str = None,
    codes: List[str] = None,
    code: str = None,
    level: Union[IntervalLevel, str] = None,
    provider: str = None,
    columns: List = None,
    col_label: dict = None,
    return_type: str = "df",
    start_timestamp: Union[pd.Timestamp, str] = None,
    end_timestamp: Union[pd.Timestamp, str] = None,
    filters: List = None,
    session: Session = None,
    order=None,
    limit: int = None,
    distinct=None,
    index: Union[str, list] = None,
    drop_index_col=False,
    time_field: str = "timestamp",
):
    """
    query data by the arguments

    :param data_schema:
    :param ids:
    :param entity_ids:
    :param entity_id:
    :param codes:
    :param code:
    :param level:
    :param provider:
    :param columns:
    :param col_label: dict with key(column), value(label)
    :param return_type: df, domain or dict. default is df. When "domain", session must be passed.
    :param start_timestamp:
    :param end_timestamp:
    :param filters:
    :param session:
    :param order:
    :param limit:
    :param index: index field name, str for single index, str list for multiple index
    :param drop_index_col: whether drop the col if it's in index, default False
    :param time_field:
    :return: results basing on return_type.
    """
    _ensure_schema_providers_loaded()
    providers = data_schema.get_providers()
    if not providers:
        raise ValueError(f"no provider registered for: {data_schema}")
    if not provider:
        provider = providers[0]

    def _query(sess):
        time_col = eval("data_schema.{}".format(time_field))

        if columns:
            # support str
            cols = list(columns)
            for i, col in enumerate(cols):
                if isinstance(col, str):
                    cols[i] = eval("data_schema.{}".format(col))

            # make sure get timestamp
            if time_col not in cols:
                cols.append(time_col)

            if col_label:
                cols_ = []
                for col in cols:
                    if col.name in col_label:
                        cols_.append(col.label(col_label.get(col.name)))
                    else:
                        cols_.append(col)
                cols = cols_

            query = sess.query(*cols)
        else:
            query = sess.query(data_schema)

        if entity_id:
            query = query.filter(data_schema.entity_id == entity_id)
        if entity_ids:
            query = query.filter(data_schema.entity_id.in_(entity_ids))
        if code:
            query = query.filter(data_schema.code == code)
        if codes:
            query = query.filter(data_schema.code.in_(codes))
        if ids:
            query = query.filter(data_schema.id.in_(ids))

        # we always store different level in different schema,the level param is not useful now
        if level:
            try:
                #: some schema has no level,just ignore it
                data_schema.level
                level_val = level.value if type(level) == IntervalLevel else level
                query = query.filter(data_schema.level == level_val)
            except Exception:
                pass

        query = common_filter(
            query,
            data_schema=data_schema,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            filters=filters,
            order=order,
            limit=limit,
            distinct=distinct,
            time_field=time_field,
        )

        if return_type == "df":
            df = pd.read_sql(query.statement, query.session.bind)
            if pd_is_not_null(df):
                if index:
                    df = index_df(df, index=index, drop=drop_index_col, time_field=time_field)
            return df
        elif return_type == "domain":
            return query.all()
        elif return_type == "dict":
            domains = query.all()
            return [_row2dict(item) for item in domains]
        elif return_type == "select":
            return query.selectable

    if session is not None:
        return _query(session)
    if return_type == "domain":
        raise ValueError(
            "session is required when return_type='domain' so returned instances stay bound. "
            "Use get_db_session() or db_session_scope() and close the session when done."
        )
    with db_session_scope(provider=provider, data_schema=data_schema) as sess:
        return _query(sess)


def data_exist(session, schema, id):
    """
    whether exist data of the id

    :param session:
    :param schema:
    :param id:
    :return:
    """
    return session.query(exists().where(and_(schema.id == id))).scalar()


def get_data_count(data_schema, filters=None, provider=None, session=None):
    """
    get record count basing on the filters

    :param data_schema:
    :param filters:
    :param session: db session (if None, uses a short-lived scope)
    :return:
    """
    if session is not None:
        query = session.query(data_schema)
        if filters:
            for filter in filters:
                query = query.filter(filter)
        count_q = query.statement.with_only_columns(func.count(data_schema.id)).order_by(None)
        return session.execute(count_q).scalar()
    with db_session_scope(provider=provider, data_schema=data_schema) as session:
        query = session.query(data_schema)
        if filters:
            for filter in filters:
                query = query.filter(filter)
        count_q = query.statement.with_only_columns(func.count(data_schema.id)).order_by(None)
        return session.execute(count_q).scalar()


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


def df_to_db(
    df: pd.DataFrame,
    data_schema: DeclarativeMeta,
    provider: str,
    force_update: bool = False,
    sub_size: int = 8000,
    drop_duplicates: bool = True,
    dtype=None,
    session=None,
    need_check=True,
) -> object:
    """
    store the df to db

    :param df: data with columns of the schema
    :param data_schema: data schema
    :param provider: data provider
    :param force_update: whether update the data with id existed
    :param sub_size: update batch size
    :param drop_duplicates: whether drop duplicates
    :return:
    """
    if not pd_is_not_null(df):
        return 0

    if drop_duplicates and df.duplicated(subset="id").any():
        logger.warning(f"remove duplicated:{df[df.duplicated()]}")
        df = df.drop_duplicates(subset="id", keep="last")

    schema_cols = get_schema_columns(data_schema)
    cols = set(df.columns.tolist()) & set(schema_cols)

    if not cols:
        print("wrong cols")
        return 0

    cols = list(cols)
    df = df[cols]

    size = len(df)

    if platform.system() == "Windows":
        sub_size = 900

    if size >= sub_size:
        step_size = int(size / sub_size)
        if size % sub_size:
            step_size = step_size + 1
    else:
        step_size = 1

    saved = 0

    def _do_save(sess):
        nonlocal saved
        for step in range(step_size):
            df_current = df.iloc[sub_size * step : sub_size * (step + 1)]

            if need_check:
                if force_update:
                    ids = df_current["id"].tolist()
                    if len(ids) == 1:
                        sql = text(f'delete from `{data_schema.__tablename__}` where id = "{ids[0]}"')
                    else:
                        sql = text(f"delete from `{data_schema.__tablename__}` where id in {tuple(ids)}")

                    sess.execute(sql)
                else:
                    current = get_data(
                        session=sess,
                        data_schema=data_schema,
                        columns=[data_schema.id],
                        provider=provider,
                        ids=df_current["id"].tolist(),
                    )
                    if pd_is_not_null(current):
                        df_current = df_current[~df_current["id"].isin(current["id"])]

            if pd_is_not_null(df_current):
                saved = saved + len(df_current)
                df_current.to_sql(
                    data_schema.__tablename__, sess.connection(), index=False, if_exists="append", dtype=dtype
                )
            sess.commit()
        return saved

    if session is not None:
        return _do_save(session)
    with db_session_scope(provider=provider, data_schema=data_schema) as sess:
        return _do_save(sess)


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
    col_label: dict = None,
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
    :param col_label:
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

    return get_data(
        data_schema=entity_schema,
        ids=ids,
        entity_ids=entity_ids,
        entity_id=entity_id,
        codes=codes,
        code=code,
        level=None,
        provider=provider,
        columns=columns,
        col_label=col_label,
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
    "common_filter",
    "del_data",
    "get_by_id",
    "get_data",
    "data_exist",
    "get_data_count",
    "decode_entity_id",
    "get_entity_type",
    "get_entity_exchange",
    "get_entity_code",
    "df_to_db",
    "get_entities",
    "get_entity_ids",
]
