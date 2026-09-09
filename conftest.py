"""Cho pytest chay tu goc repo thay vi tu trong agent/services.

Cac test import theo duong package (`agent.services.…`) vi boq_compat dung import
tuong doi — chay tu thu muc con thi Python khong biet package cha o dau.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
