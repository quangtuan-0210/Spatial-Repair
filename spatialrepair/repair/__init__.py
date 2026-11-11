"""SỬA LỖI (bước 3-5 của luồng sửa).
  fixers.py : các phép sửa cục bộ trên AST, mỗi mã lỗi một hàm
  engine.py : bộ điều phối — tìm kiếm chùm, kiểm chứng, chọn ứng viên hoặc từ chối sửa; canonical() để so 2 câu SQL
"""
from spatialrepair.repair.engine import repair, canonical, RepairResult   # noqa: F401
from spatialrepair.repair.fixers import FIXERS, _p, _r                     # noqa: F401
