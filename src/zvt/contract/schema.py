# -*- coding: utf-8 -*-
import contextlib
import inspect
import json
import logging
import platform
from datetime import timedelta
from typing import Dict, List, Union, Generator

import pandas as pd
import pkg_resources
from sqlalchemy import Column, String, DateTime, Float, func, exists, and_
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import DeclarativeMeta
from sqlalchemy.orm import Session, Query, sessionmaker
from sqlalchemy.sql.expression import text

from zvt.contract import IntervalLevel

logger = logging.getLogger(__name__)

# Session/engine state (used by get_db_engine, get_db_session, db_session_scope)
_initialized_storage_ids = set()
_bound_session_factories: dict = {}


def _get_schema_providers() -> Dict[str, List[str]]:
    """Get schema_providers merged with package default (ensures defaults are available)."""
    default = {}
    try:
        with open(pkg_resources.resource_filename("zvt", "config.json")) as f:
            default = (json.load(f).get("storage") or {}).get("schema_providers") or {}
    except Exception:
        pass
    try:
        from zvt import zvt_config

        user = (zvt_config.get("storage") or {}).get("schema_providers") or {}
        default = dict(default)
        default.update(user)
    except Exception:
        pass
    return default


from zvt.utils.time_utils import date_and_time, is_same_date_time, now_pd_timestamp, to_pd_timestamp


def common_filter(
    query: Query,
    data_schema,
    start_timestamp=None,
    end_timestamp=None,
    filters=None,
    order=None,
    limit=None,
    time_field="timestamp",
):
    """Build filter by the arguments (time range, filters, order, limit)."""
    assert data_schema is not None
    time_col = eval("data_schema.{}".format(time_field))
    if start_timestamp:
        query = query.filter(time_col >= to_pd_timestamp(start_timestamp))
    if end_timestamp:
        query = query.filter(time_col <= to_pd_timestamp(end_timestamp))
    if filters:
        for f in filters:
            query = query.filter(f)
    if order is not None:
        query = query.order_by(order)
    else:
        query = query.order_by(time_col.asc())
    if limit:
        query = query.limit(limit)
    return query


def _row2dict(row):
    d = {}
    for column in row.__table__.columns:
        d[column.name] = getattr(row, column.name)
    return d


def get_schema_columns(schema) -> List[str]:
    """Return column names of the schema table."""
    return list(schema.__table__.columns.keys())


def _ensure_schema_providers_loaded():
    """Populate zvt_context from config schema_providers for schemas without Recorders."""
    from zvt.contract import zvt_context

    if getattr(_ensure_schema_providers_loaded, "_done", False):
        return
    try:
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


def get_schema_by_name(name: str):
    """Get domain schema by class name from the global schema registry."""
    from zvt.contract import zvt_context

    for schema in zvt_context.schemas:
        if schema.__name__ == name:
            return schema


def _get_db_name(data_schema: DeclarativeMeta) -> str:
    """Resolve db name for a schema from zvt_context.dbname_map_base."""
    from zvt.contract import zvt_context

    for db_name, base in zvt_context.dbname_map_base.items():
        if issubclass(data_schema, base):
            return db_name


def _storage_backend():
    from zvt.contract import zvt_context
    from zvt.contract.storage import get_storage_backend

    return zvt_context.storage_backend or get_storage_backend()


def _route_registry():
    from zvt.contract import zvt_context
    from zvt.contract.route_registry import get_route_registry

    return zvt_context.route_registry or get_route_registry()


def get_db_engine(
    provider: str, db_name: str = None, data_schema: object = None, data_path: str = None
) -> Engine:
    """
    Get db engine from (provider, db_name) or (provider, data_schema).
    Creates tables and indexes on first use (lazy init).
    """
    from zvt import zvt_env
    from zvt.contract import zvt_context
    from zvt.contract.register import ensure_schema_tables_and_indexes

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


def _get_bound_session_factory(
    provider: str, db_name: str = None, data_schema: object = None
):
    """Get or create a sessionmaker bound to engine, cached by storage_id."""
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


def close_all_sessions() -> None:
    """Close all sessions stored in zvt_context.sessions."""
    from zvt.contract import zvt_context

    for key, session in list(zvt_context.sessions.items()):
        try:
            session.close()
        except Exception:
            logger.exception("Error closing session %s", key)
        finally:
            del zvt_context.sessions[key]


class Mixin(object):
    """
    Base class of schema.
    """

    #: id
    id = Column(String, primary_key=True)
    #: entity id
    entity_id = Column(String)

    #: the meaning could be different for different case,most time it means 'happen time'
    #: Need replace with your timezone
    timestamp = Column(DateTime)

    # unix epoch,same meaning with timestamp
    # ts = Column(BIGINT)

    @classmethod
    def help(cls):
        print(inspect.getsource(cls))

    @classmethod
    def important_cols(cls):
        return []

    @classmethod
    def time_field(cls):
        return "timestamp"

    @classmethod
    def register_recorder_cls(cls, provider, recorder_cls):
        """
        register the recorder for the schema

        :param provider:
        :param recorder_cls:
        """
        # don't make provider_map_recorder as class field,it should be created for the sub class as need
        if not hasattr(cls, "provider_map_recorder"):
            cls.provider_map_recorder = {}

        if provider not in cls.provider_map_recorder:
            cls.provider_map_recorder[provider] = recorder_cls

    @classmethod
    def register_provider(cls, provider):
        """(Deprecated) Providers now come from Recorder registration."""
        if not hasattr(cls, "_zvt_providers_override"):
            cls._zvt_providers_override = []
        if provider not in cls._zvt_providers_override:
            cls._zvt_providers_override.append(provider)

    @classmethod
    def is_internal(cls) -> bool:
        """True if schema is internal/business data, not tied to external data source."""
        return getattr(cls, "_zvt_internal", False)

    @classmethod
    def get_providers(cls) -> List[str]:
        """
        Providers: from provider_map_recorder (Recorder registration), or config storage.schema_providers.
        Only use provider_map_recorder when set on this concrete schema class (cls.__dict__), not inherited
        from a shared base like KdataCommon - otherwise StockQuote would wrongly get Stock1dKdata's providers.
        """
        if "provider_map_recorder" in cls.__dict__ and cls.provider_map_recorder:
            return list(cls.provider_map_recorder.keys())
        db_name = getattr(cls, "_zvt_db_name", None)
        if db_name:
            schema_providers = _get_schema_providers()
            if db_name in schema_providers:
                return schema_providers[db_name]
        return getattr(cls, "_zvt_providers_override", []) or []

    @classmethod
    def test_data_correctness(cls, provider, data_samples):
        for data in data_samples:
            item = cls.query_data(provider=provider, ids=[data["id"]], return_type="dict")
            print(item)
            for k in data:
                if k == "timestamp":
                    assert is_same_date_time(item[0][k], data[k])
                else:
                    assert item[0][k] == data[k]

    @classmethod
    def get_by_id(cls, session: Session, id: str):
        """Get one record by id. Caller must provide an open session."""
        return session.query(cls).get(id)

    @classmethod
    def data_exist(cls, session: Session, id: str) -> bool:
        """Whether a record with the given id exists."""
        return session.query(exists().where(and_(cls.id == id))).scalar()

    @classmethod
    def get_data_count(cls, filters=None, provider=None, session: Session = None):
        """Get record count for this schema with optional filters."""

        def _count(sess):
            query = sess.query(cls)
            if filters:
                for f in filters:
                    query = query.filter(f)
            count_q = query.statement.with_only_columns(func.count(cls.id)).order_by(None)
            return sess.execute(count_q).scalar()

        if session is not None:
            return _count(session)
        provider = provider or cls.get_providers()[0]
        with db_session_scope(provider=provider, data_schema=cls) as sess:
            return _count(sess)

    @classmethod
    def del_data(cls, filters=None, provider=None):
        """Delete records matching filters."""

        provider = provider or cls.get_providers()[0]
        with db_session_scope(provider=provider, data_schema=cls) as session:
            query = session.query(cls)
            if filters:
                for f in filters:
                    query = query.filter(f)
            query.delete()

    @classmethod
    def get_columns(cls) -> List[str]:
        """Return column names of this schema's table."""
        return list(cls.__table__.columns.keys())

    @classmethod
    def df_to_db(
        cls,
        df: pd.DataFrame,
        provider: str,
        force_update: bool = False,
        sub_size: int = 8000,
        drop_duplicates: bool = True,
        dtype=None,
        session: Session = None,
        need_check: bool = True,
    ):
        """Store the DataFrame to this schema's table."""
        from zvt.utils.pd_utils import pd_is_not_null

        if not pd_is_not_null(df):
            return 0
        if drop_duplicates and df.duplicated(subset="id").any():
            logger.warning("remove duplicated: %s", df[df.duplicated()])
            df = df.drop_duplicates(subset="id", keep="last")
        schema_cols = cls.get_columns()
        cols = list(set(df.columns.tolist()) & set(schema_cols))
        if not cols:
            logger.warning("wrong cols")
            return 0
        df = df[cols]
        size = len(df)
        if platform.system() == "Windows":
            sub_size = 900
        step_size = int(size / sub_size) + (1 if size % sub_size else 0) if size >= sub_size else 1
        saved = 0

        def _do_save(sess):
            nonlocal saved
            for step in range(step_size):
                df_current = df.iloc[sub_size * step : sub_size * (step + 1)]
                if need_check:
                    if force_update:
                        ids = df_current["id"].tolist()
                        if len(ids) == 1:
                            sql = text('delete from `{}` where id = "{}"'.format(cls.__tablename__, ids[0]))
                        else:
                            sql = text("delete from `{}` where id in {}".format(cls.__tablename__, tuple(ids)))
                        sess.execute(sql)
                    else:
                        current = cls.query_data(
                            session=sess,
                            columns=[cls.id],
                            provider=provider,
                            ids=df_current["id"].tolist(),
                        )
                        if pd_is_not_null(current):
                            df_current = df_current[~df_current["id"].isin(current["id"])]
                if pd_is_not_null(df_current):
                    saved += len(df_current)
                    df_current.to_sql(
                        cls.__tablename__, sess.connection(), index=False, if_exists="append", dtype=dtype
                    )
                sess.commit()
            return saved

        if session is not None:
            return _do_save(session)
        with db_session_scope(provider=provider, data_schema=cls) as sess:
            return _do_save(sess)

    @classmethod
    def query_data(
        cls,
        ids: List[str] = None,
        entity_ids: List[str] = None,
        entity_id: str = None,
        codes: List[str] = None,
        code: str = None,
        level: Union[IntervalLevel, str] = None,
        provider: str = None,
        columns: List = None,
        return_type: str = "df",
        start_timestamp: Union[pd.Timestamp, str] = None,
        end_timestamp: Union[pd.Timestamp, str] = None,
        filters: List = None,
        session: Session = None,
        order=None,
        limit: int = None,
        index: Union[str, list] = None,
        drop_index_col=False,
        time_field: str = "timestamp",
    ):
        """
        query data by the arguments

        :param ids:
        :param entity_ids:
        :param entity_id:
        :param codes:
        :param code:
        :param level:
        :param provider:
        :param columns:
        :param return_type: df, domain or dict. default is df
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
        from zvt.utils.pd_utils import pd_is_not_null, index_df

        _ensure_schema_providers_loaded()
        data_schema = cls
        providers = data_schema.get_providers()
        if not providers:
            raise ValueError(f"no provider registered for: {data_schema}")
        if not provider:
            provider = providers[0]

        def _query(sess):
            time_col = eval("data_schema.{}".format(time_field))

            if columns:
                cols = list(columns)
                for i, col in enumerate(cols):
                    if isinstance(col, str):
                        cols[i] = eval("data_schema.{}".format(col))
                if time_col not in cols:
                    cols.append(time_col)
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

            if level:
                try:
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
                time_field=time_field,
            )

            if return_type == "df":
                df = pd.read_sql(query.statement, query.session.bind)
                if pd_is_not_null(df) and index:
                    df = index_df(df, index=index, drop=drop_index_col, time_field=time_field)
                return df
            if return_type == "domain":
                return query.all()
            if return_type == "dict":
                domains = query.all()
                return [_row2dict(item) for item in domains]
            if return_type == "select":
                return query.selectable
            return None

        if session is not None:
            return _query(session)
        if return_type == "domain":
            raise ValueError(
                "session is required when return_type='domain' so returned instances stay bound. "
                "Use get_db_session() or db_session_scope() and close the session when done."
            )
        with db_session_scope(provider=provider, data_schema=data_schema) as sess:
            return _query(sess)

    @classmethod
    def get_storages(
        cls,
        provider: str = None,
    ):
        """
        get the storages info

        :param provider: provider
        :return: storages
        """
        if not provider:
            providers = cls.get_providers()
        else:
            providers = [provider]
        engines = []
        for p in providers:
            engines.append(get_db_engine(provider=p, data_schema=cls))
        return engines

    @classmethod
    def record_data(
        cls,
        provider_index: int = 0,
        provider: str = None,
        force_update=None,
        sleeping_time=None,
        exchanges=None,
        entity_id=None,
        entity_ids=None,
        code=None,
        codes=None,
        real_time=None,
        fix_duplicate_way=None,
        start_timestamp=None,
        end_timestamp=None,
        one_day_trading_minutes=None,
        **kwargs,
    ):
        """
        record data by the arguments

        :param entity_id:
        :param provider_index:
        :param provider:
        :param force_update:
        :param sleeping_time:
        :param exchanges:
        :param entity_ids:
        :param code:
        :param codes:
        :param real_time:
        :param fix_duplicate_way:
        :param start_timestamp:
        :param end_timestamp:
        :param one_day_trading_minutes:
        :param kwargs:
        :return:
        """
        if cls.provider_map_recorder:
            print(f"{cls.__name__} registered recorders:{cls.provider_map_recorder}")

            if provider:
                recorder_class = cls.provider_map_recorder[provider]
            else:
                recorder_class = cls.provider_map_recorder[cls.get_providers()[provider_index]]

            # get args for specific recorder class
            from zvt.contract.recorder import TimeSeriesDataRecorder

            if issubclass(recorder_class, TimeSeriesDataRecorder):
                args = [
                    item
                    for item in inspect.getfullargspec(cls.record_data).args
                    if item not in ("cls", "provider_index", "provider")
                ]
            else:
                args = ["force_update", "sleeping_time"]

            #: just fill the None arg to kw,so we could use the recorder_class default args
            kw = {}
            for arg in args:
                tmp = eval(arg)
                if tmp is not None:
                    kw[arg] = tmp

            #: FixedCycleDataRecorder
            from zvt.contract.recorder import FixedCycleDataRecorder

            if issubclass(recorder_class, FixedCycleDataRecorder):
                #: contract:
                #: 1)use FixedCycleDataRecorder to record the data with IntervalLevel
                #: 2)the table of schema with IntervalLevel format is {entity}_{level}_[adjust_type]_{event}
                table: str = cls.__tablename__
                try:
                    items = table.split("_")
                    if len(items) == 4:
                        adjust_type = items[2]
                        kw["adjust_type"] = adjust_type
                    level = IntervalLevel(items[1])
                except:
                    #: for other schema not with normal format,but need to calculate size for remaining days
                    level = IntervalLevel.LEVEL_1DAY

                kw["level"] = level

                #: add other custom args
                for k in kwargs:
                    kw[k] = kwargs[k]

                r = recorder_class(**kw)
                return r.run()
            else:
                r = recorder_class(**kw)
                return r.run()
        else:
            print(f"no recorders for {cls.__name__}")


class NormalMixin(Mixin):
    #: the record created time in db
    created_timestamp = Column(DateTime, default=pd.Timestamp.now())
    #: the record updated time in db, some recorder would check it for whether need to refresh
    updated_timestamp = Column(DateTime)


class Entity(Mixin):
    #: 标的类型
    entity_type = Column(String(length=64))
    #: 所属交易所
    exchange = Column(String(length=32))
    #: 编码
    code = Column(String(length=64))
    #: 名字
    name = Column(String(length=128))
    #: 上市日
    list_date = Column(DateTime)
    #: 退市日
    end_date = Column(DateTime)


class TradableEntity(Entity):
    """
    tradable entity
    """

    @classmethod
    def get_timezone(cls):
        """
        overwrite it to get the timezone of the entity

        :return: pytz timezone
        """

        return None

    @classmethod
    def get_trading_dates(cls, start_date=None, end_date=None):
        """
        overwrite it to get the trading dates of the entity

        :param start_date:
        :param end_date:
        :return: list of dates
        """
        return pd.date_range(start_date, end_date, freq="B")

    @classmethod
    def get_trading_intervals(cls, include_bidding_time=False):
        """
        overwrite it to get the trading intervals of the entity

        :return: list of time intervals, in format [(start,end)]
        """
        if include_bidding_time:
            return [("09:15", "11:30"), ("13:00", "15:00")]
        else:
            return [("09:30", "11:30"), ("13:00", "15:00")]

    @classmethod
    def in_real_trading_time(cls, timestamp=None, include_bidding_time=True):
        if not timestamp:
            timestamp = now_pd_timestamp(tz=cls.get_timezone())
        else:
            timestamp = pd.Timestamp(timestamp, tz=cls.get_timezone())
        for open_close in cls.get_trading_intervals(include_bidding_time=include_bidding_time):
            open_time = date_and_time(the_date=timestamp.date(), the_time=open_close[0], tz=cls.get_timezone())
            close_time = date_and_time(the_date=timestamp.date(), the_time=open_close[1], tz=cls.get_timezone())
            if open_time <= timestamp <= close_time:
                return True
            else:
                continue
        return False

    @classmethod
    def before_trading_time(cls, timestamp=None):
        if not timestamp:
            timestamp = now_pd_timestamp(tz=cls.get_timezone())
        else:
            timestamp = pd.Timestamp(timestamp, tz=cls.get_timezone())
        open_time = date_and_time(
            the_date=timestamp.date(),
            the_time=cls.get_trading_intervals(include_bidding_time=True)[0][0],
            tz=cls.get_timezone(),
        )
        return timestamp < open_time

    @classmethod
    def after_trading_time(cls, timestamp=None):
        if not timestamp:
            timestamp = now_pd_timestamp(tz=cls.get_timezone())
        else:
            timestamp = pd.Timestamp(timestamp, tz=cls.get_timezone())
        close_time = date_and_time(
            the_date=timestamp.date(),
            the_time=cls.get_trading_intervals(include_bidding_time=True)[-1][1],
            tz=cls.get_timezone(),
        )
        return timestamp > close_time

    @classmethod
    def in_trading_time(cls, timestamp=None):
        if not timestamp:
            timestamp = now_pd_timestamp(tz=cls.get_timezone())
        else:
            timestamp = pd.Timestamp(timestamp, tz=cls.get_timezone())
        open_time = date_and_time(
            the_date=timestamp.date(),
            the_time=cls.get_trading_intervals(include_bidding_time=True)[0][0],
            tz=cls.get_timezone(),
        )
        close_time = date_and_time(
            the_date=timestamp.date(),
            the_time=cls.get_trading_intervals(include_bidding_time=True)[-1][1],
            tz=cls.get_timezone(),
        )
        return open_time <= timestamp <= close_time

    @classmethod
    def get_close_hour_and_minute(cls):
        hour, minute = cls.get_trading_intervals()[-1][1].split(":")
        return int(hour), int(minute)

    @classmethod
    def get_interval_timestamps(cls, start_date, end_date, level: IntervalLevel):
        """
        generate the timestamps for the level

        :param start_date:
        :param end_date:
        :param level:
        """

        for current_date in cls.get_trading_dates(start_date=start_date, end_date=end_date):
            if level == IntervalLevel.LEVEL_1DAY:
                yield current_date
            elif level == IntervalLevel.LEVEL_1WEEK:
                if current_date.weekday() == 4:
                    yield current_date
            else:
                start_end_list = cls.get_trading_intervals()

                for start_end in start_end_list:
                    start = start_end[0]
                    end = start_end[1]

                    current_timestamp = date_and_time(the_date=current_date, the_time=start)
                    end_timestamp = date_and_time(the_date=current_date, the_time=end)

                    while current_timestamp <= end_timestamp:
                        yield current_timestamp
                        current_timestamp = current_timestamp + timedelta(minutes=level.to_minute())

    @classmethod
    def is_open_timestamp(cls, timestamp):
        timestamp = pd.Timestamp(timestamp)
        return is_same_date_time(
            timestamp,
            date_and_time(the_date=timestamp.date(), the_time=cls.get_trading_intervals()[0][0]),
        )

    @classmethod
    def is_close_timestamp(cls, timestamp):
        timestamp = pd.Timestamp(timestamp)
        return is_same_date_time(
            timestamp,
            date_and_time(the_date=timestamp.date(), the_time=cls.get_trading_intervals()[-1][1]),
        )

    @classmethod
    def is_finished_kdata_timestamp(cls, timestamp: pd.Timestamp, level: IntervalLevel):
        """
        :param timestamp: the timestamp could be recorded in kdata of the level
        :type timestamp: pd.Timestamp
        :param level:
        :type level: zvt.domain.common.IntervalLevel
        :return:
        :rtype: bool
        """
        timestamp = pd.Timestamp(timestamp)

        for t in cls.get_interval_timestamps(timestamp.date(), timestamp.date(), level=level):
            if is_same_date_time(t, timestamp):
                return True

        return False

    @classmethod
    def could_short(cls):
        """
        whether could be shorted

        :return:
        """
        return False

    @classmethod
    def get_trading_t(cls):
        """
        0 means t+0
        1 means t+1

        :return:
        """
        return 1


class ActorEntity(Entity):
    pass


class NormalEntityMixin(TradableEntity):
    #: the record created time in db
    created_timestamp = Column(DateTime, default=pd.Timestamp.now())
    #: the record updated time in db, some recorder would check it for whether need to refresh
    updated_timestamp = Column(DateTime)


class Portfolio(TradableEntity):
    """
    composition of tradable entities
    """

    @classmethod
    def get_stocks(
        cls,
        code=None,
        codes=None,
        ids=None,
        timestamp=now_pd_timestamp(),
        provider=None,
    ):
        """
        the publishing policy of portfolio positions is different for different types,
        overwrite this function for get the holding stocks in specific date

        :param code: portfolio(etf/block/index...) code
        :param codes: portfolio(etf/block/index...) codes
        :param ids: portfolio(etf/block/index...) ids
        :param timestamp: the date of the holding stocks
        :param provider: the data provider
        :return:
        """
        schema_str = f"{cls.__name__}Stock"
        portfolio_stock = get_schema_by_name(schema_str)
        return portfolio_stock.query_data(provider=provider, code=code, codes=codes, timestamp=timestamp, ids=ids)


#: 组合(Fund,Etf,Index,Block等)和个股(Stock)的关系 应该继承自该类
#: 该基础类可以这样理解:
#: entity为组合本身,其包含了stock这种entity,timestamp为持仓日期,从py的"你知道你在干啥"的哲学出发，不加任何约束
class PortfolioStock(Mixin):
    #: portfolio标的类型
    entity_type = Column(String(length=64))
    #: portfolio所属交易所
    exchange = Column(String(length=32))
    #: portfolio编码
    code = Column(String(length=64))
    #: portfolio名字
    name = Column(String(length=128))

    stock_id = Column(String)
    stock_code = Column(String(length=64))
    stock_name = Column(String(length=128))


#: 支持时间变化,报告期标的调整
class PortfolioStockHistory(PortfolioStock):
    #: 报告期,season1,half_year,season3,year
    report_period = Column(String(length=32))
    #: 3-31,6-30,9-30,12-31
    report_date = Column(DateTime)

    #: 占净值比例
    proportion = Column(Float)
    #: 持有股票的数量
    shares = Column(Float)
    #: 持有股票的市值
    market_cap = Column(Float)


#: 交易标的和参与者的关系应该继承自该类, meet,遇见,恰如其分的诠释参与者和交易标的的关系
#: 市场就是参与者与交易标的的关系，类的命名规范为{Entity}{relation}{Entity}，entity_id代表"所"为的entity,"受"者entity以具体类别的id命名
#: 比如StockTopTenHolder:TradableMeetActor中entity_id和actor_id,分别代表股票和股东
class TradableMeetActor(Mixin):
    #: tradable code
    code = Column(String(length=64))
    #: tradable name
    name = Column(String(length=128))

    actor_id = Column(String)
    actor_type = Column(String)
    actor_code = Column(String(length=64))
    actor_name = Column(String(length=128))


#: 也可以"所"为参与者，"受"为标的
class ActorMeetTradable(Mixin):
    #: actor code
    code = Column(String(length=64))
    #: actor name
    name = Column(String(length=128))

    tradable_id = Column(String)
    tradable_type = Column(String)
    tradable_code = Column(String(length=64))
    tradable_name = Column(String(length=128))


# the __all__ is generated
__all__ = [
    "Mixin",
    "NormalMixin",
    "Entity",
    "TradableEntity",
    "ActorEntity",
    "NormalEntityMixin",
    "Portfolio",
    "PortfolioStock",
    "PortfolioStockHistory",
    "TradableMeetActor",
    "ActorMeetTradable",
    "get_schema_by_name",
    "get_schema_columns",
    "get_db_engine",
    "get_db_session",
    "db_session_scope",
    "close_all_sessions",
]
