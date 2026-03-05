# -*- coding: utf-8 -*-
import logging
from typing import List, Optional

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

import zvt.contract.api as contract_api
from zvt.api.selector import get_entity_ids_by_filter
from zvt.contract.api import decode_entity_id
from zvt.domain import BlockStock, Block, Stock, Stockus, Stockhk
from zvt.tag.common import TagType, TagStatsQueryType, StockPoolType, InsertMode
from zvt.tag.tag_models import (
    SetStockTagsModel,
    CreateStockPoolInfoModel,
    CreateStockPoolsModel,
    QueryStockTagStatsModel,
    QuerySimpleStockTagsModel,
    ActivateSubTagsModel,
    BatchSetStockTagsModel,
    TagParameter,
    CreateTagInfoModel,
    StockTagOptions,
    ChangeMainTagModel,
    BuildMainTagIndustryRelationModel,
)
from zvt.tag.tag_schemas import (
    StockTags,
    StockPools,
    StockPoolInfo,
    TagStats,
    StockSystemTags,
    MainTagInfo,
    SubTagInfo,
    HiddenTagInfo,
    IndustryInfo,
)
from zvt.tag.tag_utils import (
    get_sub_tags,
    get_stock_pool_names,
    get_main_tag_by_sub_tag,
    get_main_tag_by_industry,
)
from zvt.utils.time_utils import to_pd_timestamp, to_date_time_str, current_date, now_pd_timestamp
from zvt.utils.utils import fill_dict, compare_dicts, flatten_list

logger = logging.getLogger(__name__)

# Tag 模块统一使用 zvt provider 的 stock_tags 库，session 可由 FastAPI 依赖注入
TAG_DB_PROVIDER = "zvt"


def _with_tag_session(session: Optional[Session], data_schema, fn):
    """有 session 时用传入的 session 执行 fn(session)（不 commit）；否则用 db_session_scope 执行并由 scope commit。"""
    if session is not None:
        return fn(session)
    with contract_api.db_session_scope(provider=TAG_DB_PROVIDER, data_schema=data_schema) as sess:
        return fn(sess)


def _stock_tags_need_update(stock_tags: StockTags, set_stock_tags_model: SetStockTagsModel):
    if (
        stock_tags.main_tag != set_stock_tags_model.main_tag
        or stock_tags.main_tag_reason != set_stock_tags_model.main_tag_reason
        or stock_tags.sub_tag != set_stock_tags_model.sub_tag
        or stock_tags.sub_tag_reason != set_stock_tags_model.sub_tag_reason
        or not compare_dicts(stock_tags.active_hidden_tags, set_stock_tags_model.active_hidden_tags)
    ):
        return True
    return False


def get_stock_tag_options(entity_id: str, session: Optional[Session] = None) -> StockTagOptions:
    def _do(sess: Session) -> StockTagOptions:
        datas: List[dict] = StockTags.query_data(
            entity_id=entity_id, order=StockTags.timestamp.desc(), limit=1, return_type="dict", session=sess
        )
        main_tag_options = []
        sub_tag_options = []
        hidden_tag_options = []

        main_tag = None
        sub_tag = None
        active_hidden_tags = None
        stock_tags = None
        if datas:
            stock_tags = datas[0]
            main_tag = stock_tags.get("main_tag")
            sub_tag = stock_tags.get("sub_tag")

            main_tags = stock_tags.get("main_tags")
            if main_tags:
                main_tag_options = [
                    CreateTagInfoModel(tag=tag, tag_reason=tag_reason)
                    for tag, tag_reason in main_tags.items()
                ]

            sub_tags = stock_tags.get("sub_tags")
            if sub_tags:
                sub_tag_options = [
                    CreateTagInfoModel(tag=tag, tag_reason=tag_reason)
                    for tag, tag_reason in sub_tags.items()
                ]

            if stock_tags.get("active_hidden_tags"):
                active_hidden_tags = stock_tags["active_hidden_tags"]

            hidden_tags = stock_tags.get("hidden_tags")
            if hidden_tags:
                hidden_tag_options = [
                    CreateTagInfoModel(tag=tag, tag_reason=tag_reason)
                    for tag, tag_reason in hidden_tags.items()
                ]

        main_tags_info: List[dict] = MainTagInfo.query_data(session=sess, return_type="dict")
        if not main_tag and main_tags_info:
            main_tag = main_tags_info[0]["tag"]

        main_tags_set = (stock_tags or {}).get("main_tags") or {}
        main_tag_options = main_tag_options + [
            CreateTagInfoModel(tag=item["tag"], tag_reason=item["tag_reason"])
            for item in main_tags_info
            if item["tag"] not in main_tags_set
        ]

        sub_tags_info: List[dict] = SubTagInfo.query_data(session=sess, return_type="dict")
        if not sub_tag and sub_tags_info:
            sub_tag = sub_tags_info[0]["tag"]
        sub_tags_set = (stock_tags or {}).get("sub_tags") or {}
        sub_tag_options = sub_tag_options + [
            CreateTagInfoModel(tag=item["tag"], tag_reason=item["tag_reason"])
            for item in sub_tags_info
            if item["tag"] not in sub_tags_set
        ]

        hidden_tags_info: List[dict] = HiddenTagInfo.query_data(session=sess, return_type="dict")
        hidden_tags_set = (stock_tags or {}).get("hidden_tags") or {}
        hidden_tag_options = hidden_tag_options + [
            CreateTagInfoModel(tag=item["tag"], tag_reason=item["tag_reason"])
            for item in hidden_tags_info
            if item["tag"] not in hidden_tags_set
        ]

        return StockTagOptions(
            main_tag=main_tag,
            sub_tag=sub_tag,
            active_hidden_tags=active_hidden_tags,
            main_tag_options=main_tag_options,
            sub_tag_options=sub_tag_options,
            hidden_tag_options=hidden_tag_options,
        )

    return _with_tag_session(session, StockTags, _do)


def build_stock_tags(
    set_stock_tags_model: SetStockTagsModel,
    timestamp: pd.Timestamp,
    set_by_user: bool,
    keep_current=False,
    session: Optional[Session] = None,
):
    logger.info(set_stock_tags_model)

    def _do(sess: Session):
        main_tag_info = CreateTagInfoModel(
            tag=set_stock_tags_model.main_tag, tag_reason=set_stock_tags_model.main_tag_reason
        )
        if not is_tag_info_existed(tag=main_tag_info.tag, tag_type=TagType.main_tag, session=sess):
            create_tag_info(tag_info=main_tag_info, tag_type=TagType.main_tag, session=sess)

        if set_stock_tags_model.sub_tag:
            sub_tag_info = CreateTagInfoModel(
                tag=set_stock_tags_model.sub_tag, tag_reason=set_stock_tags_model.sub_tag_reason
            )
            if not is_tag_info_existed(tag=sub_tag_info.tag, tag_type=TagType.sub_tag, session=sess):
                create_tag_info(tag_info=sub_tag_info, tag_type=TagType.sub_tag, session=sess)

        if set_stock_tags_model.active_hidden_tags:
            for tag in set_stock_tags_model.active_hidden_tags:
                hidden_tag_info = CreateTagInfoModel(
                    tag=tag, tag_reason=set_stock_tags_model.active_hidden_tags.get(tag)
                )
                if not is_tag_info_existed(tag=hidden_tag_info.tag, tag_type=TagType.hidden_tag, session=sess):
                    create_tag_info(tag_info=hidden_tag_info, tag_type=TagType.hidden_tag, session=sess)

        entity_id = set_stock_tags_model.entity_id
        main_tags = {}
        sub_tags = {}
        hidden_tags = {}

        entity_type, _, _ = decode_entity_id(entity_id)

        datas = StockTags.query_data(
            session=sess,
            entity_id=entity_id,
            filters=[StockTags.entity_type == entity_type],
            limit=1,
            return_type="domain",
        )

        if datas:
            current_stock_tags: StockTags = datas[0]

            # nothing change
            if not _stock_tags_need_update(current_stock_tags, set_stock_tags_model):
                logger.info(f"Not change stock_tags for {set_stock_tags_model.entity_id}")
                return current_stock_tags

            if current_stock_tags.main_tags:
                main_tags = dict(current_stock_tags.main_tags)
            if current_stock_tags.sub_tags:
                sub_tags = dict(current_stock_tags.sub_tags)
            if current_stock_tags.hidden_tags:
                hidden_tags = dict(current_stock_tags.hidden_tags)

        else:
            current_stock_tags = StockTags(
                entity_type=entity_type,
                id=f"{entity_id}_tags",
                entity_id=entity_id,
                timestamp=timestamp,
            )

        # update tag
        if not keep_current:
            current_stock_tags.main_tag = set_stock_tags_model.main_tag
            current_stock_tags.main_tag_reason = set_stock_tags_model.main_tag_reason

            if set_stock_tags_model.sub_tag:
                current_stock_tags.sub_tag = set_stock_tags_model.sub_tag
            if set_stock_tags_model.sub_tag_reason:
                current_stock_tags.sub_tag_reason = set_stock_tags_model.sub_tag_reason
            # could update to None
            current_stock_tags.active_hidden_tags = set_stock_tags_model.active_hidden_tags
        # update tags
        main_tags[set_stock_tags_model.main_tag] = set_stock_tags_model.main_tag_reason
        if set_stock_tags_model.sub_tag:
            sub_tags[set_stock_tags_model.sub_tag] = set_stock_tags_model.sub_tag_reason
        if set_stock_tags_model.active_hidden_tags:
            for k, v in set_stock_tags_model.active_hidden_tags.items():
                hidden_tags[k] = v
        current_stock_tags.main_tags = main_tags
        current_stock_tags.sub_tags = sub_tags
        current_stock_tags.hidden_tags = hidden_tags

        current_stock_tags.set_by_user = set_by_user

        sess.add(current_stock_tags)
        # 由 _with_tag_session 的 scope 或 FastAPI 依赖统一 commit
        sess.refresh(current_stock_tags)
        return current_stock_tags

    return _with_tag_session(session, StockTags, _do)


def build_tag_parameter(tag_type: TagType, tag, tag_reason, stock_tag: StockTags):
    hidden_tag = None
    hidden_tag_reason = None

    if tag_type == TagType.main_tag:
        main_tag = tag
        if main_tag in stock_tag.main_tags:
            main_tag_reason = stock_tag.main_tags.get(main_tag, tag_reason)
        else:
            main_tag_reason = tag_reason
        sub_tag = stock_tag.sub_tag
        sub_tag_reason = stock_tag.sub_tag_reason
    elif tag_type == TagType.sub_tag:
        sub_tag = tag
        if sub_tag in stock_tag.sub_tags:
            sub_tag_reason = stock_tag.sub_tags.get(sub_tag, tag_reason)
        else:
            sub_tag_reason = tag_reason
        main_tag = stock_tag.main_tag
        main_tag_reason = stock_tag.main_tag_reason
    elif tag_type == TagType.hidden_tag:
        hidden_tag = tag
        if stock_tag.hidden_tags and (hidden_tag in stock_tag.hidden_tags):
            hidden_tag_reason = stock_tag.hidden_tags.get(hidden_tag, tag_reason)
        else:
            hidden_tag_reason = tag_reason

        sub_tag = stock_tag.sub_tag
        sub_tag_reason = stock_tag.sub_tag_reason

        main_tag = stock_tag.main_tag
        main_tag_reason = stock_tag.main_tag_reason

    else:
        assert False

    return TagParameter(
        main_tag=main_tag,
        main_tag_reason=main_tag_reason,
        sub_tag=sub_tag,
        sub_tag_reason=sub_tag_reason,
        hidden_tag=hidden_tag,
        hidden_tag_reason=hidden_tag_reason,
    )


def batch_set_stock_tags(
    batch_set_stock_tags_model: BatchSetStockTagsModel, session: Optional[Session] = None
):
    if not batch_set_stock_tags_model.entity_ids:
        return []

    def _do(sess: Session):
        tag_info = CreateTagInfoModel(
            tag=batch_set_stock_tags_model.tag, tag_reason=batch_set_stock_tags_model.tag_reason
        )
        if not is_tag_info_existed(
            tag=tag_info.tag, tag_type=batch_set_stock_tags_model.tag_type, session=sess
        ):
            create_tag_info(tag_info=tag_info, tag_type=batch_set_stock_tags_model.tag_type, session=sess)

        tag_type = batch_set_stock_tags_model.tag_type
        if tag_type == TagType.main_tag:
            main_tag = batch_set_stock_tags_model.tag
            stock_tags: List[StockTags] = StockTags.query_data(
                entity_ids=batch_set_stock_tags_model.entity_ids,
                filters=[StockTags.main_tag != main_tag],
                session=sess,
                return_type="domain",
            )
        elif tag_type == TagType.sub_tag:
            sub_tag = batch_set_stock_tags_model.tag
            stock_tags: List[StockTags] = StockTags.query_data(
                entity_ids=batch_set_stock_tags_model.entity_ids,
                filters=[StockTags.sub_tag != sub_tag],
                session=sess,
                return_type="domain",
            )
        elif tag_type == TagType.hidden_tag:
            hidden_tag = batch_set_stock_tags_model.tag
            stock_tags: List[StockTags] = StockTags.query_data(
                entity_ids=batch_set_stock_tags_model.entity_ids,
                filters=[func.json_extract(StockTags.active_hidden_tags, f'$."{hidden_tag}"') == None],
                session=sess,
                return_type="domain",
            )

        for stock_tag in stock_tags:
            tag_parameter: TagParameter = build_tag_parameter(
                tag_type=tag_type,
                tag=batch_set_stock_tags_model.tag,
                tag_reason=batch_set_stock_tags_model.tag_reason,
                stock_tag=stock_tag,
            )
            if tag_type == TagType.hidden_tag:
                active_hidden_tags = {batch_set_stock_tags_model.tag: batch_set_stock_tags_model.tag_reason}
            else:
                active_hidden_tags = stock_tag.active_hidden_tags

            set_stock_tags_model = SetStockTagsModel(
                entity_id=stock_tag.entity_id,
                main_tag=tag_parameter.main_tag,
                main_tag_reason=tag_parameter.main_tag_reason,
                sub_tag=tag_parameter.sub_tag,
                sub_tag_reason=tag_parameter.sub_tag_reason,
                active_hidden_tags=active_hidden_tags,
            )

            build_stock_tags(
                set_stock_tags_model=set_stock_tags_model,
                timestamp=now_pd_timestamp(),
                set_by_user=True,
                keep_current=False,
                session=sess,
            )
            sess.refresh(stock_tag)
        return stock_tags

    return _with_tag_session(session, StockTags, _do)


def build_default_main_tag(entity_type="stock", entity_ids=None, force_rebuild=False):
    """
    build default main tag by industry

    :param entity_type:
    :param entity_ids: entity ids
    :param force_rebuild: always rebuild it if True otherwise only build which not existed
    """
    if not entity_ids:
        entity_ids = get_entity_ids_by_filter(
            entity_type=entity_type, provider="em", ignore_delist=True, ignore_st=False, ignore_new_stock=False
        )

    if entity_type == "stock":
        df_block = Block.query_data(provider="em", filters=[Block.category == "industry"])
        industry_codes = df_block["code"].tolist()
        block_stocks: List[BlockStock] = BlockStock.query_data(
            provider="em",
            filters=[BlockStock.code.in_(industry_codes), BlockStock.stock_id.in_(entity_ids)],
            return_type="dict",
        )
        entity_industry_mapping = {block_stock['stock_id']: block_stock['name'] for block_stock in block_stocks}
    elif entity_type == "stockus":
        datas: List[Stockus] = Stockus.query_data(entity_ids=entity_ids, return_type="dict")
        entity_industry_mapping = {item['entity_id']: item['industry'] for item in datas}
    elif entity_type == "stockhk":
        datas: List[Stockhk] = Stockhk.query_data(entity_ids=entity_ids, return_type="dict")
        entity_industry_mapping = {item['entity_id']: item['industry'] for item in datas}
    else:
        raise ValueError(f"Unsupported entity_type: {entity_type}")

    for entity_id in entity_ids:
        stock_tags: List[StockTags] = StockTags.query_data(entity_id=entity_id, return_type="dict")
        if not force_rebuild and stock_tags:
            logger.info(f"{entity_id} main tag has been set.")
            continue

        logger.info(f"build main tag for: {entity_id}")

        industry = entity_industry_mapping.get(entity_id)
        if industry:
            main_tag = get_main_tag_by_industry(industry_name=industry)
            main_tag_reason = f"来自行业:{industry}"
        else:
            main_tag = "其他"
            main_tag_reason = "其他"

        build_stock_tags(
            set_stock_tags_model=SetStockTagsModel(
                entity_id=entity_id,
                main_tag=main_tag,
                main_tag_reason=main_tag_reason,
                sub_tag=None,
                sub_tag_reason=None,
                active_hidden_tags=None,
            ),
            timestamp=now_pd_timestamp(),
            set_by_user=False,
            keep_current=False,
        )


def build_default_sub_tags(entity_ids=None):
    if not entity_ids:
        entity_ids = get_entity_ids_by_filter(
            provider="em", ignore_delist=True, ignore_st=False, ignore_new_stock=False
        )

    for entity_id in entity_ids:
        logger.info(f"build sub tag for: {entity_id}")
        datas: List[dict] = StockTags.query_data(entity_id=entity_id, limit=1, return_type="dict")
        if not datas:
            raise AssertionError(f"Main tag must be set at first for {entity_id}")

        current_stock_tags = datas[0]
        keep_current = False
        if current_stock_tags.get("set_by_user"):
            logger.info(f"keep current tags set by user for: {entity_id}")
            keep_current = True

        current_sub_tag = current_stock_tags.get("sub_tag")
        filters = [BlockStock.stock_id == entity_id]
        sub_tags = current_stock_tags.get("sub_tags") or {}
        if current_sub_tag:
            logger.info(f"{entity_id} current_sub_tag: {current_sub_tag}")
            current_sub_tags = list(sub_tags.keys())
            filters = filters + [BlockStock.name.notin_(current_sub_tags)]

        df_block = Block.query_data(provider="em", filters=[Block.category == "concept"])
        concept_codes = df_block["code"].tolist()
        filters = filters + [BlockStock.code.in_(concept_codes)]

        block_stocks: List[dict] = BlockStock.query_data(
            provider="em",
            filters=filters,
            return_type="dict",
        )
        if not block_stocks:
            logger.info(f"no block_stocks for: {entity_id}")
            continue

        for block_stock in block_stocks:
            sub_tag = block_stock["name"]
            if sub_tag in get_sub_tags():
                sub_tag_reason = f"来自概念:{sub_tag}"

                main_tag = get_main_tag_by_sub_tag(sub_tag)
                main_tag_reason = sub_tag_reason
                if (main_tag == "其他" or not main_tag) and current_stock_tags.get("main_tag"):
                    main_tag = current_stock_tags["main_tag"]
                    main_tag_reason = current_stock_tags.get("main_tag_reason") or main_tag_reason

                build_stock_tags(
                    set_stock_tags_model=SetStockTagsModel(
                        entity_id=entity_id,
                        main_tag=main_tag,
                        main_tag_reason=main_tag_reason,
                        sub_tag=sub_tag,
                        sub_tag_reason=sub_tag_reason,
                        active_hidden_tags=current_stock_tags.get("active_hidden_tags"),
                    ),
                    timestamp=now_pd_timestamp(),
                    set_by_user=False,
                    keep_current=keep_current,
                )
            else:
                logger.info(f"ignore {sub_tag} not in sub_tag_info yet")


def get_tag_info_schema(tag_type: TagType):
    if tag_type == TagType.main_tag:
        data_schema = MainTagInfo
    elif tag_type == TagType.sub_tag:
        data_schema = SubTagInfo
    elif tag_type == TagType.hidden_tag:
        data_schema = HiddenTagInfo
    else:
        assert False

    return data_schema


def is_tag_info_existed(tag: str, tag_type: TagType, session: Optional[Session] = None) -> bool:
    data_schema = get_tag_info_schema(tag_type=tag_type)

    def _do(sess: Session) -> bool:
        current_tags_info = data_schema.query_data(
            session=sess, filters=[data_schema.tag == tag], return_type="dict"
        )
        return bool(current_tags_info)

    return _with_tag_session(session, data_schema, _do)


def create_tag_info(tag_info: CreateTagInfoModel, tag_type: TagType, session: Optional[Session] = None):
    """
    Create tags info
    """
    if is_tag_info_existed(tag=tag_info.tag, tag_type=tag_type, session=session):
        raise HTTPException(status_code=409, detail=f"This tag has been registered in {tag_type}")

    data_schema = get_tag_info_schema(tag_type=tag_type)

    def _do(sess: Session):
        timestamp = current_date()
        entity_id = "admin"
        tag_info_db = data_schema(
            id=f"admin_{tag_info.tag}",
            entity_id=entity_id,
            timestamp=timestamp,
            tag=tag_info.tag,
            tag_reason=tag_info.tag_reason,
        )
        sess.add(tag_info_db)
        # 由 _with_tag_session 的 scope 或 FastAPI 依赖统一 commit
        sess.refresh(tag_info_db)
        return tag_info_db

    return _with_tag_session(session, data_schema, _do)


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
        sess.refresh(stock_pool_info)
        return stock_pool_info

    return _with_tag_session(session, StockPoolInfo, _do)


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
        # one instance per day for entity_type
        stock_pool_id = f"{entity_type}_{stock_pool_name}_{to_date_time_str(target_date)}"
        datas: List[StockPools] = StockPools.query_data(
            session=sess,
            filters=[
                StockPools.id == stock_pool_id,
            ],
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
        sess.refresh(stock_pool)
        return stock_pool

    return _with_tag_session(session, StockPools, _do)


def delete_stock_pool(stock_pool_name: str, session: Optional[Session] = None):
    def _do(sess: Session):
        stock_pool_info: List = StockPoolInfo.query_data(
            session=sess,
            filters=[StockPoolInfo.stock_pool_name == stock_pool_name],
            return_type="domain",
        )

        contract_api.del_data(data_schema=StockPools, filters=[StockPools.stock_pool_name == stock_pool_name])

        if stock_pool_info:
            sess.delete(stock_pool_info[0])
            return "success"
        return "not found"

    return _with_tag_session(session, StockPoolInfo, _do)


def get_main_tags_in_stock_pool(stock_pool_name: str, session: Optional[Session] = None) -> List[str]:
    def _do(sess: Session) -> List[str]:
        stock_pool_info: List[dict] = StockPoolInfo.query_data(
            session=sess,
            filters=[StockPoolInfo.stock_pool_name == stock_pool_name],
            return_type="dict",
        )
        if not stock_pool_info:
            raise HTTPException(status_code=404, detail=f"Stock pool info {stock_pool_name} not found")

        entity_ids = None
        if stock_pool_name != "all":
            stock_pools: List[dict] = StockPools.query_data(
                session=sess,
                filters=[StockPools.stock_pool_name == stock_pool_name],
                order=StockPools.timestamp.desc(),
                limit=1,
                return_type="dict",
            )
            if not stock_pools:
                raise HTTPException(status_code=404, detail=f"Stock pool {stock_pool_name} not found")

            entity_ids = stock_pools[0].get("entity_ids")
            if not entity_ids:
                return []

        df = StockTags.query_data(
            session=sess,
            entity_ids=entity_ids,
            columns=["main_tag", "entity_id"],
            return_type="df",
        )
        grouped_df = df.groupby("main_tag").agg(entity_count=("entity_id", "count")).reset_index()
        sorted_df = grouped_df.sort_values(by=["entity_count"], ascending=[False])
        return sorted_df["main_tag"].tolist()

    return _with_tag_session(session, StockPools, _do)


def query_stock_tag_stats(
    query_stock_tag_stats_model: QueryStockTagStatsModel, session: Optional[Session] = None
):
    def _do(sess: Session):
        datas: List[dict] = TagStats.query_data(
            session=sess,
            filters=[TagStats.stock_pool_name == query_stock_tag_stats_model.stock_pool_name],
            order=TagStats.timestamp.desc(),
            limit=1,
            return_type="dict",
        )
        if not datas:
            return []

        target_date = datas[0]["timestamp"]

        tag_stats_list: List[dict] = TagStats.query_data(
            session=sess,
            filters=[
                TagStats.stock_pool_name == query_stock_tag_stats_model.stock_pool_name,
                TagStats.timestamp == target_date,
            ],
            return_type="dict",
            order=TagStats.position.asc(),
        )

        if query_stock_tag_stats_model.query_type == TagStatsQueryType.simple:
            return tag_stats_list

        entity_ids = flatten_list([tag_stats["entity_ids"] for tag_stats in tag_stats_list])

        # get stocks meta (dict to avoid session/domain lifecycle)
        stocks: List[dict] = Stock.query_data(provider="em", entity_ids=entity_ids, return_type="dict")
        entity_map = {item["entity_id"]: item for item in stocks}

        # get stock tags
        tags_dict = StockTags.query_data(
            session=sess,
            filters=[StockTags.entity_id.in_(entity_ids)],
            return_type="dict",
        )
        entity_tags_map = {item["entity_id"]: item for item in tags_dict}

        # get stock system tags
        system_tags_dict = StockSystemTags.query_data(
            session=sess,
            filters=[StockSystemTags.timestamp == target_date, StockSystemTags.entity_id.in_(entity_ids)],
            return_type="dict",
        )
        entity_system_tags_map = {item["entity_id"]: item for item in system_tags_dict}

        for tag_stats in tag_stats_list:
            stock_details = []
            for entity_id in tag_stats["entity_ids"]:
                stock_meta = entity_map.get(entity_id) or {}
                stock_details_model = {
                    "entity_id": entity_id,
                    "main_tag": tag_stats["main_tag"],
                    "code": stock_meta.get("code"),
                    "name": stock_meta.get("name"),
                }

                stock_tags = entity_tags_map.get(entity_id)
                stock_details_model["sub_tag"] = stock_tags["sub_tag"]
                if stock_tags["active_hidden_tags"] is not None:
                    stock_details_model["hidden_tags"] = stock_tags["active_hidden_tags"].keys()
                else:
                    stock_details_model["hidden_tags"] = None

                stock_system_tags = entity_system_tags_map.get(entity_id)
                stock_details_model = fill_dict(stock_system_tags, stock_details_model)

                stock_details.append(stock_details_model)
            tag_stats["stock_details"] = stock_details

        return tag_stats_list

    return _with_tag_session(session, TagStats, _do)


def refresh_main_tag_by_sub_tag(
    stock_tag: StockTags, set_by_user=False, session: Optional[Session] = None
) -> StockTags:
    if not stock_tag.sub_tags:
        logger.warning(f"{stock_tag.entity_id} has no sub_tags yet")
        return stock_tag

    sub_tag = stock_tag.sub_tag
    sub_tag_reason = stock_tag.sub_tags[sub_tag]

    main_tag = get_main_tag_by_sub_tag(sub_tag)
    main_tag_reason = sub_tag_reason
    if main_tag == "其他":
        main_tag = stock_tag.main_tag
        main_tag_reason = stock_tag.main_tag_reason

    set_stock_tags_model = SetStockTagsModel(
        entity_id=stock_tag.entity_id,
        main_tag=main_tag,
        main_tag_reason=main_tag_reason,
        sub_tag=sub_tag,
        sub_tag_reason=sub_tag_reason,
        active_hidden_tags=stock_tag.active_hidden_tags,
    )
    logger.info(f"set_stock_tags_model:{set_stock_tags_model}")

    return build_stock_tags(
        set_stock_tags_model=set_stock_tags_model,
        timestamp=stock_tag.timestamp,
        set_by_user=set_by_user,
        keep_current=False,
        session=session,
    )


def refresh_all_main_tag_by_sub_tag():
    with contract_api.db_session_scope(provider="zvt", data_schema=StockTags) as session:
        stock_tags = StockTags.query_data(
            session=session,
            return_type="domain",
        )
        for stock_tag in stock_tags:
            refresh_main_tag_by_sub_tag(stock_tag)


def reset_to_default_main_tag(current_main_tag: str):
    df = StockTags.query_data(
        filters=[StockTags.main_tag == current_main_tag],
        columns=[StockTags.entity_id],
        return_type="df",
    )
    entity_ids = df["entity_id"].tolist()
    if not entity_ids:
        logger.info(f"all stocks with main_tag: {current_main_tag} has been reset")
        return
    build_default_main_tag(entity_ids=entity_ids, force_rebuild=True)


def activate_industry_list(industry_list: List[str]):
    df_block = Block.query_data(provider="em", filters=[Block.category == "industry", Block.name.in_(industry_list)])
    industry_codes = df_block["code"].tolist()
    block_stocks: List[BlockStock] = BlockStock.query_data(
        provider="em",
        filters=[BlockStock.code.in_(industry_codes)],
        return_type="dict",
    )
    entity_ids = [block_stock['stock_id'] for block_stock in block_stocks]

    if not entity_ids:
        logger.info(f"No stocks in {industry_list}")
        return

    build_default_main_tag(entity_ids=entity_ids, force_rebuild=True)


def activate_sub_tags(activate_sub_tags_model: ActivateSubTagsModel, session: Optional[Session] = None):
    sub_tags = activate_sub_tags_model.sub_tags

    def _do(sess: Session):
        result = {}
        for sub_tag in sub_tags:
            entity_ids = None

            stock_tags = StockTags.query_data(
                session=sess,
                entity_ids=entity_ids,
                filters=[func.json_extract(StockTags.sub_tags, f'$."{sub_tag}"') != None],
                return_type="domain",
            )
            if not stock_tags:
                logger.info(f"all stocks with sub_tag: {sub_tag} has been activated")
                continue
            for stock_tag in stock_tags:
                stock_tag.sub_tag = sub_tag
                sess.refresh(stock_tag)
                result[stock_tag.entity_id] = refresh_main_tag_by_sub_tag(
                    stock_tag, set_by_user=True, session=sess
                )
        return result

    return _with_tag_session(session, StockTags, _do)


def delete_main_tag(main_tag: str, session: Optional[Session] = None):
    def _do(sess: Session):
        stock_tags = StockTags.query_data(
            session=sess,
            filters=[func.json_extract(StockTags.main_tags, f'$."{main_tag}"') != None],
            return_type="domain",
        )

        sql = text('update industry_info set main_tag = "其他" where main_tag = :tag')
        sess.execute(sql, {"tag": main_tag})

        sql = text('update sub_tag_info set main_tag = "其他" where main_tag = :tag')
        sess.execute(sql, {"tag": main_tag})

        for stock_tag in stock_tags:
            logger.info(f"remove main_tag: {main_tag} for {stock_tag.entity_id}")

            main_tags = dict(stock_tag.main_tags)
            main_tags.pop(main_tag, None)
            stock_tag.main_tags = main_tags

            if main_tag == stock_tag.main_tag:
                if main_tags:
                    stock_tag.main_tag = list(main_tags.keys())[0]
                    stock_tag.main_tag_reason = main_tags[stock_tag.main_tag]
                else:
                    stock_tag.main_tag = "其他"
                    stock_tag.main_tag_reason = "其他"

    return _with_tag_session(session, StockTags, _do)


def delete_sub_tag(sub_tag: str, session: Optional[Session] = None):
    def _do(sess: Session):
        stock_tags = StockTags.query_data(
            session=sess,
            filters=[func.json_extract(StockTags.sub_tags, f'$."{sub_tag}"') != None],
            return_type="domain",
        )

        for stock_tag in stock_tags:
            logger.info(f"remove sub_tag: {sub_tag} for {stock_tag.entity_id}")

            sub_tags = dict(stock_tag.sub_tags)
            sub_tags.pop(sub_tag, None)
            stock_tag.sub_tags = sub_tags

            if sub_tag == stock_tag.sub_tag:
                if sub_tags:
                    stock_tag.sub_tag = list(sub_tags.keys())[0]
                    stock_tag.sub_tag_reason = sub_tags[stock_tag.sub_tag]
                else:
                    stock_tag.sub_tag = "其他"
                    stock_tag.sub_tag_reason = "其他"

    return _with_tag_session(session, StockTags, _do)


def delete_hidden_tag(hidden_tag: str, session: Optional[Session] = None):
    def _do(sess: Session):
        stock_tags = StockTags.query_data(
            session=sess,
            filters=[func.json_extract(StockTags.hidden_tags, f'$."{hidden_tag}"') != None],
            return_type="domain",
        )

        for stock_tag in stock_tags:
            logger.info(f"delete hidden_tag: {hidden_tag} for {stock_tag.entity_id}")

            hidden_tags = dict(stock_tag.hidden_tags)
            hidden_tags.pop(hidden_tag, None)
            stock_tag.hidden_tags = hidden_tags

            if stock_tag.active_hidden_tags and (hidden_tag in stock_tag.active_hidden_tags):
                active_hidden_tags = dict(stock_tag.active_hidden_tags)
                active_hidden_tags.pop(hidden_tag)
                stock_tag.active_hidden_tags = active_hidden_tags

    return _with_tag_session(session, StockTags, _do)


def delete_tag(tag: str, tag_type: TagType, session: Optional[Session] = None):
    data_schema = get_tag_info_schema(tag_type=tag_type)

    def _do(sess: Session):
        current_tags_info = data_schema.query_data(
            session=sess, filters=[data_schema.tag == tag], return_type="domain"
        )

        if not current_tags_info:
            logger.info(f"tag_type: {tag_type}, tag: {tag} not exists, ignore delete tag info")
        else:
            logger.info(f"delete tag info, tag_type: {tag_type}, tag: {tag} ")
            sess.delete(current_tags_info[0])

        if tag_type == TagType.main_tag:
            delete_main_tag(main_tag=tag, session=sess)
        elif tag_type == TagType.sub_tag:
            delete_sub_tag(sub_tag=tag, session=sess)
        elif tag_type == TagType.hidden_tag:
            delete_hidden_tag(hidden_tag=tag, session=sess)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported tag type: {tag_type}")

    return _with_tag_session(session, data_schema, _do)


def _create_main_tag_if_not_existed(main_tag, main_tag_reason, session: Optional[Session] = None):
    main_tag_info = CreateTagInfoModel(tag=main_tag, tag_reason=main_tag_reason)
    if not is_tag_info_existed(tag=main_tag_info.tag, tag_type=TagType.main_tag, session=session):
        create_tag_info(tag_info=main_tag_info, tag_type=TagType.main_tag, session=session)


def get_main_tag_industry_relation(main_tag, session: Optional[Session] = None):
    def _do(sess: Session):
        df = IndustryInfo.query_data(
            session=sess,
            columns=[IndustryInfo.industry_name],
            filters=[IndustryInfo.main_tag == main_tag],
            return_type="df",
        )
        return {"main_tag": main_tag, "industry_list": df["industry_name"].tolist()}

    return _with_tag_session(session, StockTags, _do)


def get_main_tag_sub_tag_relation(main_tag, session: Optional[Session] = None):
    def _do(sess: Session):
        df = SubTagInfo.query_data(
            session=sess,
            columns=[SubTagInfo.tag],
            filters=[SubTagInfo.main_tag == main_tag],
            return_type="df",
        )
        return {"main_tag": main_tag, "sub_tag_list": df["tag"].tolist()}

    return _with_tag_session(session, StockTags, _do)


def build_main_tag_industry_relation(
    build_relation_model: BuildMainTagIndustryRelationModel, session: Optional[Session] = None
):
    def _do(sess: Session):
        main_tag = build_relation_model.main_tag
        _create_main_tag_if_not_existed(main_tag=main_tag, main_tag_reason=main_tag, session=sess)

        industry_list = build_relation_model.industry_list

        datas: List[IndustryInfo] = IndustryInfo.query_data(
            session=sess,
            filters=[IndustryInfo.main_tag == main_tag, IndustryInfo.industry_name.notin_(industry_list)],
            return_type="domain",
        )
        for data in datas:
            data.main_tag = "其他"

        industry_info_list: List[IndustryInfo] = IndustryInfo.query_data(
            session=sess,
            filters=[IndustryInfo.industry_name.in_(industry_list)],
            return_type="domain",
        )
        for industry_info in industry_info_list:
            industry_info.main_tag = main_tag

        if build_relation_model.activate:
            activate_industry_list(industry_list=industry_list)

    return _with_tag_session(session, StockTags, _do)


def build_main_tag_sub_tag_relation(
    build_relation_model: BuildMainTagIndustryRelationModel, session: Optional[Session] = None
):
    def _do(sess: Session):
        main_tag = build_relation_model.main_tag
        _create_main_tag_if_not_existed(main_tag=main_tag, main_tag_reason=main_tag, session=sess)

        sub_tag_list = build_relation_model.sub_tag_list

        datas: List[SubTagInfo] = SubTagInfo.query_data(
            session=sess,
            filters=[SubTagInfo.main_tag == main_tag, SubTagInfo.tag.notin_(sub_tag_list)],
            return_type="domain",
        )
        for data in datas:
            data.main_tag = "其他"

        sub_tag_info_list: List[SubTagInfo] = SubTagInfo.query_data(
            session=sess,
            filters=[SubTagInfo.tag.in_(sub_tag_list)],
            return_type="domain",
        )
        for sub_tag_info in sub_tag_info_list:
            sub_tag_info.main_tag = main_tag

        if build_relation_model.activate:
            activate_sub_tags(ActivateSubTagsModel(sub_tags=sub_tag_list), session=sess)

    return _with_tag_session(session, SubTagInfo, _do)


def change_main_tag(change_main_tag_model: ChangeMainTagModel, session: Optional[Session] = None):
    current_main_tag = change_main_tag_model.current_main_tag
    new_main_tag = change_main_tag_model.new_main_tag

    def _do(sess: Session):
        _create_main_tag_if_not_existed(main_tag=new_main_tag, main_tag_reason=new_main_tag, session=sess)

        stock_tags: List[StockTags] = StockTags.query_data(
            filters=[StockTags.main_tag == current_main_tag],
            session=sess,
            return_type="domain",
        )

        for stock_tag in stock_tags:
            tag_parameter: TagParameter = build_tag_parameter(
                tag_type=TagType.main_tag,
                tag=new_main_tag,
                tag_reason=new_main_tag,
                stock_tag=stock_tag,
            )
            set_stock_tags_model = SetStockTagsModel(
                entity_id=stock_tag.entity_id,
                main_tag=tag_parameter.main_tag,
                main_tag_reason=tag_parameter.main_tag_reason,
                sub_tag=tag_parameter.sub_tag,
                sub_tag_reason=tag_parameter.sub_tag_reason,
                active_hidden_tags=stock_tag.active_hidden_tags,
            )

            build_stock_tags(
                set_stock_tags_model=set_stock_tags_model,
                timestamp=now_pd_timestamp(),
                set_by_user=True,
                keep_current=False,
                session=sess,
            )
            sess.refresh(stock_tag)
        return stock_tags

    return _with_tag_session(session, StockTags, _do)


def query_simple_stock_tags(
    query_simple_stock_tags_model: QuerySimpleStockTagsModel, session: Optional[Session] = None
) -> List[dict]:
    """
    查询简单股票标签列表（供 REST 与任务/脚本共用）。
    REST 调用时传入 session；任务/脚本调用不传 session，内部会开 scope。
    """
    entity_ids = query_simple_stock_tags_model.entity_ids
    filters = [StockTags.entity_id.in_(entity_ids)]

    def _do(sess: Session) -> List[dict]:
        tags: List[dict] = StockTags.query_data(
            session=sess,
            filters=filters,
            return_type="dict",
            order=StockTags.timestamp.desc(),
        )
        entity_tag_map = {item["entity_id"]: item for item in tags}
        result_tags = []
        stocks: List[dict] = Stock.query_data(
            provider="em", entity_ids=[t["entity_id"] for t in tags], return_type="dict"
        )
        stocks_map = {item["entity_id"]: item for item in stocks}
        for entity_id in entity_ids:
            tag = entity_tag_map.get(entity_id)
            if not tag:
                continue
            stock_meta = stocks_map.get(entity_id) or {}
            result_tags.append({
                **tag,
                "name": stock_meta.get("name"),
                "controlling_holder_parent": stock_meta.get("controlling_holder_parent")
                or stock_meta.get("controlling_holder"),
                "top_ten_ratio": stock_meta.get("top_ten_ratio"),
            })
        return result_tags

    return _with_tag_session(session, StockTags, _do)


if __name__ == "__main__":
    # print(get_main_tags_in_stock_pool("涨停梯队"))
    # print(delete_tag(tag="赛马概念", tag_type=TagType.sub_tag))
    activate_industry_list(industry_list=["航天航空"])
    # activate_sub_tags(ActivateSubTagsModel(sub_tags=["航天概念", "天基互联", "北斗导航", "通用航空"]))
    # build_default_main_tag(entity_type="stockhk")

# the __all__ is generated
__all__ = [
    "get_stock_tag_options",
    "query_simple_stock_tags",
    "build_stock_tags",
    "build_tag_parameter",
    "batch_set_stock_tags",
    "build_default_main_tag",
    "build_default_sub_tags",
    "get_tag_info_schema",
    "is_tag_info_existed",
    "create_tag_info",
    "build_stock_pool_info",
    "build_stock_pool",
    "query_stock_tag_stats",
    "refresh_main_tag_by_sub_tag",
    "refresh_all_main_tag_by_sub_tag",
    "reset_to_default_main_tag",
    "activate_industry_list",
    "activate_sub_tags",
]
