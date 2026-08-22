#!/usr/bin/env python3
"""A-E 5 scheme offline evaluation."""
import json, sys, time, os, pickle, re
import jieba, requests, chromadb

LEGACY_DB = "/nvme/home/rincug/qinhaozhe/backend/data/databases/user_result_cloud"
CLEAN_DB  = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
BM25_DIR  = "/nvme/home/rincug/qhz/backend/data/bm25_v2"