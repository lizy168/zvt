# -*- coding: utf-8 -*-
from enum import Enum


class StockPoolType(Enum):
    system = "system"
    custom = "custom"
    dynamic = "dynamic"


__all__ = ["StockPoolType"]
