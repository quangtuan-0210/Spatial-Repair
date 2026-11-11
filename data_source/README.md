# data_source — phần gốc FloodSQL-Bench dùng cho SpatialRepair

Nguồn: D:/Projects/FloodSQL-Bench-main (bản tải về 26/05/2026).
Gốc trên mạng: https://huggingface.co/datasets/HanzhouLiu/FloodSQL-Bench, arXiv 2512.12084.
KHÔNG sửa các file trong thư mục này. Muốn ghi chú hay chỉnh sửa thì tạo file mới ở chỗ khác.

## Nội dung
- benchmark/floodsql_443.jsonl: file chính, 443 câu. Mỗi câu có id, question, sql, level (L0–L5),
  category, output_type, expected_columns, gold_result, gold_row_count.
  File này gộp từ by_level/*.json, còn gold_result lấy từ bechmark_updated.jsonl.
- benchmark/by_level/: các file .json gốc theo từng nhóm câu hỏi
  (L4 dùng bản triple_table_key_spatial_updated, đúng như tác giả khuyên).
- benchmark/bechmark_updated.jsonl: bản 443 câu gốc, có kết quả chạy chuẩn.
- benchmark/README_original.md: ghi chú của tác giả, có phần các hạn chế đã biết.
- metadata/metadata_parquet.json: metadata của 10 bảng và mục _global (luật chung).
  File có ngày 01/06, có thể đã được sinh lại bằng meta_builder.py, nên đối chiếu với bản trên HF nếu cần.
- scripts_metadata/: code tạo metadata gốc.
- paths.json: đường dẫn tới các file parquet. Không chép sang đây vì floodplain đã nặng khoảng 2,5 GB.

## Ghi chú về không gian
- Toàn bộ geometry dùng EPSG:4326 / OGC:CRS84 (kinh độ, vĩ độ), lưu dạng DuckDB Spatial GEOMETRY (WKB).
  Vì vậy ST_Area và ST_Distance trả về đơn vị **độ**, không phải mét.
- hospitals và schools không có cột geometry. Điểm được dựng bằng ST_Point(LON, LAT).
- Engine là DuckDB + extension spatial, không phải PostGIS.
