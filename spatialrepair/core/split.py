"""Chia benchmark thành dev / test cố định (băm theo id câu hỏi, cân theo từng mức L0-L5).
dev  : được nhìn để chỉnh luật / ngưỡng.
test : chỉ dùng để báo cáo, KHÔNG nhìn khi chỉnh.
"""
import hashlib


def split_of(qid, salt="spatialrepair-2026"):
    h = int(hashlib.sha1(f"{salt}:{qid}".encode()).hexdigest(), 16)
    return "dev" if h % 2 == 0 else "test"
