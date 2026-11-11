"""HẠ TẦNG dùng chung.
  config.py  : đọc file .env
  db.py      : kết nối DuckDB + spatial, tạo VIEW cho 10 bảng parquet, chạy SQL có giới hạn thời gian
  cache.py   : lưu kết quả mỗi câu SQL (sqlite) để không chạy lại
  compare.py : so kết quả 2 câu SQL (Execution Accuracy), có tính 'bằng điểm' (tie)
  ties.py    : dò các câu ORDER BY ... LIMIT có bằng điểm
  split.py   : chia dev / test cố định
"""
