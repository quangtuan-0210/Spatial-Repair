# data_source — FloodSQL-Bench

Nguồn: https://huggingface.co/datasets/HanzhouLiu/FloodSQL-Bench (arXiv 2512.12084). Giấy phép: `LICENSE_FloodSQL-Bench`.

- `benchmark/floodsql_443.jsonl`: 443 câu, mỗi câu có id, question, sql (chuẩn), level (L0–L5), gold_result.
- `metadata/metadata_parquet.json`: metadata của 10 bảng và luật chung (dùng để dựng KG và viết prompt).
- `paths.json`: tên file parquet của từng bảng và thư mục chứa chúng (`parquet_dir`); có thể ghi đè bằng `PARQUET_DIR` trong `.env`.

Toàn bộ hình học dùng EPSG:4326 (kinh độ, vĩ độ), nên ST_Area / ST_Distance trả về đơn vị độ.
hospitals và schools không có cột hình học dùng được: điểm dựng bằng ST_Point(LON, LAT).
