# SpatialRepair

Sửa lỗi câu **Spatial SQL do LLM sinh ra** bằng **đồ thị tri thức (KG)** và **sửa cục bộ trên cây cú pháp (AST)**,
đánh giá trên [FloodSQL-Bench](https://huggingface.co/datasets/HanzhouLiu/FloodSQL-Bench) (443 câu hỏi, DuckDB + spatial).
Khi sửa, SpatialRepair **không gọi LLM**.

## Kết quả chính

Trên SQL do Qwen3.5-122B-A10B sinh cho 443 câu FloodSQL-Bench:

| Phương pháp | Execution Accuracy (%) | Sửa đúng (câu sai → đúng) | Làm hỏng (câu đúng → sai) | Gọi LLM / câu |
|---|---|---|---|---|
| Không sửa | 51,9 | – | – | 0 |
| LLM tự sửa (self-refine) | 54,9 | 8,0% | 1,7% | 1 |
| LLM sửa theo lỗi DuckDB (exec-feedback) | 55,3 | 7,0% | 0% | 0,13 |
| Luật viết tay, không KG | 52,4 | 0,9% | 0% | 0 |
| KG chỉ có lược đồ quan hệ | 52,6 | 1,4% | 0% | 0 |
| **SpatialRepair** | **66,4** | **30,5%** | **0,4%** | **0** |

Kết quả đầy đủ (theo mức L0–L5, theo loại lỗi, độ trễ, token, tỷ lệ từ chối):
`results/exp/qwen35/summary.md` (lỗi thật của LLM) và `results/exp/mut/summary.md` (1355 lỗi đột biến có nhãn).
Độ chính xác phát hiện lỗi trên tập có nhãn (443 SQL chuẩn + 1355 đột biến): Precision 98,6%, Recall 95,1%, F1 96,8%.

## Cách hoạt động

1. Phân tích câu SQL thành AST (thư viện `sqlglot`, cú pháp DuckDB).
2. **Phát hiện lỗi** bằng hai nguồn: luật tra trong KG (cột, hình học, hệ tọa độ, hệ mã vùng, miền giá trị)
   và đối chiếu với ý định câu hỏi (mã vùng, quan hệ không gian, câu hỏi đếm, diện tích).
3. **Sinh ứng viên sửa** cục bộ cho từng lỗi, tìm kiếm chùm tối đa 4 bước.
4. **Kiểm chứng**: chỉ nhận bản sửa hết lỗi theo KG và chạy được trên DuckDB; không có thì **từ chối**, giữ câu gốc.

Thiết kế KG: `docs/kg_design.md`. Ghi chú phát hiện / sửa: `docs/detection_notes.md`, `docs/repair_notes.md`.

## Các loại lỗi

| Mã | Lỗi | Mã | Lỗi |
|---|---|---|---|
| S1 | sai vị từ không gian | K1 | sai mã vùng (bang / quận) so với câu hỏi |
| S2 | đảo đối số Contains / Within | K2 | cắt GEOID sai độ dài |
| S3 | đảo kinh độ / vĩ độ trong ST_Point | K3 | ép GEOID sang số (mất số 0 đầu) |
| S4 | cột hình học thiếu hoặc rỗng | K4 | thiếu lọc miền giá trị / giá trị rác −999 |
| S5 | nhầm diện tích phần giao với cả hình | K5 | sai chữ hoa / thường |
| S6 | đổi hệ tọa độ / đơn vị không được yêu cầu | G | lỗi SQL chung: cột, bí danh không tồn tại |
| S7 | thiếu lọc ST_IsValid trên hình hỏng | | |
| S8 | nối bằng ZIP thay vì ZCTA | | |
| S9 | đếm trùng sau phép nối không gian | | |

## Cấu trúc thư mục

```
spatialrepair/          MÃ NGUỒN
├── core/               kết nối DuckDB, cache, so kết quả (có tính đồng hạng), chia dev/test, đọc .env
├── knowledge/          đồ thị tri thức: dựng + tra cứu
├── detection/          phát hiện lỗi: luật KG (checks.py) + đối chiếu câu hỏi (intent.py)
├── repair/             phép sửa (fixers.py) + bộ điều phối tìm kiếm / kiểm chứng (engine.py)
├── benchmark/          sinh đột biến (cài lỗi có kiểm soát)
├── baselines/          phương pháp đối chứng: luật viết tay, LLM self-refine, LLM exec-feedback
└── __main__.py         dòng lệnh: python -m spatialrepair ...
kg/                     KG đã dựng: kg_spatial.json (834 nút), kg_relational.json (289 nút)
data_source/            phần gốc FloodSQL-Bench: 443 câu hỏi + SQL chuẩn + metadata (không gồm file parquet)
bench/                  kho SQL ban đầu: initial_qwen35 (LLM sinh), initial_mut (1355 đột biến có nhãn), đột biến
analysis/tie_info.json  thông tin câu có kết quả đồng hạng (dùng khi chấm)
results/exp/            kết quả 6 phương pháp trên từng kho (mỗi câu một dòng jsonl + summary.md)
docs/                   thiết kế KG, ghi chú phát hiện và sửa
tests/                  kiểm tra nhanh bộ phát hiện
```

## Cài đặt

Cần Python ≥ 3.10.

```bash
git clone https://github.com/quangtuan-0210/Spatial-Repair.git
cd Spatial-Repair
python -m venv .venv
# Windows:  .venv\Scripts\activate.bat
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Kiểm tra nhanh (không cần dữ liệu, không cần LLM):

```bash
python tests/test_checks_mutations.py
# -> 16/16 đột biến bị bắt đúng
```

## Hướng dẫn chạy

### 1. Phát hiện lỗi trong một câu SQL

Không cần dữ liệu parquet. Lấy câu hỏi và SQL ban đầu theo mã câu từ kho Qwen3.5:

```bash
python -m spatialrepair detect --qid L1_0002
```

Hoặc tự đưa câu hỏi và SQL:

```bash
python -m spatialrepair detect \
  --question "How many schools in Louisiana (STATEFP 22) are inside FEMA floodplain polygons?" \
  --sql "SELECT COUNT(*) FROM schools s JOIN floodplain f ON ST_Contains(f.geometry, ST_Point(s.LON, s.LAT)) WHERE s.STATEFP = '22'"
```

### 2. Sửa một câu SQL

```bash
python -m spatialrepair repair --qid L4_0006 --show-gold
```

In ra lỗi phát hiện được, các phép sửa đã áp dụng và SQL sau sửa (`--show-gold` in kèm SQL chuẩn để so).
Các tùy chọn khác:

| Tùy chọn | Ý nghĩa |
|---|---|
| `--store mut` | lấy SQL ban đầu từ kho đột biến thay vì kho Qwen3.5 |
| `--kg relational` | dùng KG chỉ có lược đồ quan hệ (để so sánh) |
| `--no-question` | không đối chiếu câu hỏi, chỉ dùng luật KG |
| `--run` | chạy thử ứng viên trên DuckDB trước khi chọn (cần dữ liệu parquet, xem mục 4) |

### 3. Xem kết quả thực nghiệm đã chạy

```bash
python -m spatialrepair show --store qwen35   # lỗi thật của LLM, 443 câu
python -m spatialrepair show --store mut      # 1355 đột biến có nhãn, có bảng theo loại lỗi
```

### 4. Chạy với dữ liệu thật (DuckDB)

Cần thiết cho `repair --run` và để chấm kết quả.

1. Tải các file parquet của FloodSQL-Bench từ [Hugging Face](https://huggingface.co/datasets/HanzhouLiu/FloodSQL-Bench)
   (10 bảng; riêng floodplain khoảng 2,5 GB).
2. Chép `.env.example` thành `.env`, điền `PARQUET_DIR=<thư mục chứa các file parquet>`
   (hoặc sửa `parquet_dir` trong `data_source/paths.json`). Tên file của từng bảng nằm trong `data_source/paths.json`.
3. Chạy:

```bash
python -m spatialrepair repair --qid L5_0010 --run
```

### 5. Dùng như thư viện Python

```python
from spatialrepair.knowledge.kg import KG
from spatialrepair.detection.intent import detect
from spatialrepair.repair import repair

kg = KG("kg/kg_spatial.json")
question = "Which 5 Louisiana (STATEFP 22) counties contain the largest number of schools located in floodplain areas?"
sql = """SELECT c.NAME, COUNT(*) AS n FROM schools s
JOIN county c ON ST_Contains(c.geometry, ST_Point(s.LON, s.LAT))
JOIN floodplain f ON ST_Contains(f.geometry, ST_Point(s.LON, s.LAT))
WHERE c.STATEFP = '22' GROUP BY c.NAME ORDER BY n DESC LIMIT 5"""

for f in detect(sql, question, kg):
    print(f.level, f.code, f.msg)

r = repair(sql, question, kg)            # thêm executor=... để chạy thử ứng viên trên DuckDB
print(r.changed, r.edits)
print(r.sql_out)
```

Muốn chạy thử ứng viên: `from spatialrepair.core.db import connect, run_sql`, rồi
`con, _ = connect()` và truyền `executor=lambda s: run_sql(con, s, 60)` vào `repair(...)`.

### 6. Phương pháp đối chứng dùng LLM

Các phương pháp `self_refine` và `exec_feedback` nằm trong `spatialrepair/baselines/` và gọi LLM qua API chuẩn OpenAI.
Điền `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` vào `.env` (mẫu trong `.env.example`). File `.env` không được đưa lên git.

## Dữ liệu và giấy phép

FloodSQL-Bench thuộc về tác giả gốc (arXiv 2512.12084), giấy phép trong `data_source/LICENSE_FloodSQL-Bench`.
Thư mục `data_source/` giữ nguyên các file gốc, chỉ thêm `floodsql_443.jsonl` (gộp 443 câu) và `paths.json`.
