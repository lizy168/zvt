# -*- coding: utf-8 -*-
"""
Shared declarative base for all apps schemas (tag, trading, etc.).
All app tables use this base so they can be registered to one DB (zvt_apps).
"""
from sqlalchemy.orm import declarative_base

AppsBase = declarative_base()
