"""SpatialRepair: sửa lỗi Text-to-Spatial SQL bằng đồ thị tri thức (KG) + sửa cục bộ trên AST, đánh giá trên FloodSQL-Bench.

Gói con:
  core/       hạ tầng: kết nối DuckDB, cache kết quả, so kết quả (có tính tie), chia dev/test, đọc file .env
  knowledge/  đồ thị tri thức: dựng từ metadata + dữ liệu thật, hàm tra cứu
  detection/  PHÁT HIỆN lỗi: luật dựa trên KG (checks) + đối chiếu ý câu hỏi (intent)
  repair/     SỬA lỗi: các phép sửa (fixers) + bộ điều phối (engine)
  benchmark/  sinh đột biến (cài lỗi có kiểm soát) cho bộ đánh giá
  baselines/  phương pháp đối chứng: luật viết tay, gọi LLM, prompt, danh sách 6 phương pháp (+1 biến thể tùy chọn)
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from spatialrepair.core.config import load_env  # noqa: E402

load_env()   # tự đọc file .env ở thư mục gốc (không ghi đè biến môi trường đã có)
