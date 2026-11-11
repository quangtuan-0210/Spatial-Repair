# Bảng phân loại lỗi Spatial SQL cho SpatialRepair (bản nháp v0)

Nguồn: 443 SQL chuẩn của FloodSQL-Bench, `metadata_parquet.json` và ý nghĩa các hàm DuckDB Spatial.
KHÔNG dùng kết quả LLM nào. Số liệu lấy từ `analysis/gold_sql_stats.json`, sinh bằng `scripts/1_chuan_bi/profile_gold_sql.py`.

## 1. Những gì SQL chuẩn cho thấy

| Đặc điểm | Số câu |
|---|---|
| Câu có dùng hàm không gian | 258/443 (toàn bộ L2, L4, L5; 12 câu L0; 2 câu L1; 1 câu L3) |
| `ST_IsValid` (lọc hình hợp lệ) | 235 |
| `ST_Point(LON, LAT)` để dựng điểm cho schools/hospitals | 113 (đều đúng thứ tự LON, LAT) |
| `ST_Intersects` / `ST_Contains` / `ST_Within` / `ST_Overlaps` / `ST_Touches` | 104 / 73 / 56 / 17 / 12 |
| `ST_Contains(đa giác, điểm)` / `ST_Within(điểm, đa giác)` | 69 / 42 |
| `ST_Area` | 39 (trong đó 28 câu là `ST_Area(ST_Intersection(...))`) |
| `ST_Distance`, `ST_DWithin`, `ST_Buffer`, `ST_Transform`, `::geography`, đổi đơn vị | **0** |
| `COUNT(DISTINCT ...)` | 200 |
| Lọc theo khóa địa lý: `STATEFP` / `LEFT(GEOID,5)` / `GEOID LIKE` | 307 / 119 / 57 |
| `CAST ... AS DOUBLE` / `IS NOT NULL` | 56 / 129 |

### Ba phát hiện ảnh hưởng tới đề cương
1. **Không có câu nào hỏi về khoảng cách, cũng không có câu nào đổi đơn vị.** Mọi diện tích đều tính bằng **độ vuông** trên EPSG:4326.
   Ví dụ L0_0025 hỏi "diện tích > 0.5", tức 0.5 độ vuông. Metadata còn ghi rõ: tính diện tích trên geometry gốc, không dùng `ST_Transform`.
   → Lỗi CRS/đơn vị trên benchmark này không phải kiểu "quên đổi sang mét". Nó là kiểu **LLM tự ý đổi** (thêm `ST_Transform`, `::geography`,
   `ST_Area_Spheroid`, nhân hệ số), làm kết quả lệch khỏi SQL chuẩn. Cách sửa đúng ở đây là **bỏ phép đổi đi**.
   Nếu luận cứ trong đề cương nhấn mạnh "CRS và đơn vị", nên viết lại cho khớp với dữ liệu này, hoặc bổ sung một bộ câu hỏi về khoảng cách.
2. **Cách diễn đạt trong câu hỏi không khớp một-một với hàm.** Có 41 câu nói "overlap": 23 câu dùng `ST_Intersects`, chỉ 17 câu dùng `ST_Overlaps`.
   Với chữ "touch/adjacent", ngoài 10 câu dùng `ST_Touches` còn có 6 câu dùng `ST_Intersects` và 3 câu dùng `ST_Overlaps`.
   → Nếu bộ sửa đổi hàm theo đúng nghĩa của từ, nó có thể "sửa" một câu đúng thành sai so với SQL chuẩn. Rủi ro này đo bằng regression rate (RQ4).
   Cần quy ước rõ: "overlap area" → `ST_Intersects` + `ST_Area(ST_Intersection)`.
3. **Metadata tự mâu thuẫn:** phần mô tả `ST_Area` ghi đơn vị là "square meters", nhưng CRS lại là EPSG:4326, tức đơn vị là độ.
   Khi dựng đồ thị tri thức, phải lấy CRS làm chuẩn và ghi chú lại chỗ mâu thuẫn này.

## 2. Bảng phân loại lỗi

Cột "Cơ hội" là số câu chuẩn có thể cài loại lỗi đó vào (dùng cho phần đột biến có kiểm soát).

### Nhóm S — Lỗi không gian (trọng tâm của SpatialRepair)

| Mã | Loại lỗi | Ví dụ sai → đúng | KG cần biết gì để phát hiện | Thao tác sửa trên AST | Cơ hội |
|---|---|---|---|---|---|
| S1 | Chọn sai hàm quan hệ | `ST_Overlaps` ↔ `ST_Intersects` ↔ `ST_Touches` | Nghĩa của từng hàm, quy ước từ ngữ ("overlap area", "share boundary") | Thay tên hàm | ~200 |
| S2 | Đảo chiều đối số | `ST_Contains(point, poly)` → `ST_Contains(poly, point)` | Kiểu hình học từng vế (điểm/đa giác), chiều của hàm | Đổi chỗ 2 đối số hoặc Contains↔Within | 111 |
| S3 | Đảo tọa độ khi dựng điểm | `ST_Point(LAT, LON)` → `ST_Point(LON, LAT)` | Cột LON là x, LAT là y (EPSG:4326) | Đổi chỗ 2 đối số | 113 |
| S4 | Dùng cột hình học không có | `schools.geometry` → `ST_Point(s.LON, s.LAT)` | Bảng nào có geometry, bảng nào chỉ có LAT/LON | Thay nút cột bằng biểu thức dựng điểm | 113 |
| S5 | Sai phép đo diện tích | `ST_Area(a.geom)` ↔ `ST_Area(ST_Intersection(a,b))` | Câu hỏi hỏi diện tích phần giao hay diện tích cả hình | Bọc thêm hoặc bỏ `ST_Intersection` | 39 |
| S6 | Tự ý đổi CRS/đơn vị | thêm `ST_Transform`, `::geography`, `*1e-6` → bỏ đi | CRS gốc, luật "dùng geometry gốc", đơn vị là độ | Gỡ nút đổi CRS/đơn vị | 39 |
| S7 | Thiếu hoặc thừa bước lọc hình hợp lệ | thiếu `ST_IsValid(geom)` | Luật: hình học phải hợp lệ trước khi join | Thêm/bỏ điều kiện trong WHERE | 235 |
| S8 | Join không gian bị thay bằng join khóa (hoặc ngược lại) | ZCTA nối bằng ZIP dạng text → nối bằng đa giác | Danh sách 14 cặp join không gian và 14 cặp join khóa | Đổi điều kiện ON | ~250 |
| S9 | Đếm trùng sau join không gian | `COUNT(*)` → `COUNT(DISTINCT a.GEOID)` | Join không gian là nhiều-nhiều | Thêm DISTINCT hoặc GROUP BY | 200 |

### Nhóm K — Lỗi khóa địa lý và miền giá trị (KG cũng kiểm tra được)

| Mã | Loại lỗi | Ví dụ | KG cần biết gì | Thao tác sửa | Cơ hội |
|---|---|---|---|---|---|
| K1 | Sai mã vùng (FIPS) | Harris = '201', TX = '48' | Bảng mã STATEFP/COUNTYFP | Thay hằng số | 307 |
| K2 | Sai cách cắt GEOID | `LEFT(GEOID,2)` khi cần lấy quận; nối tract với county mà không dùng `LEFT(...,5)` | Cấu trúc GEOID 2+3+6 chữ số | Thay biểu thức khóa | 176 |
| K3 | Sai kiểu khóa | `CAST(GEOID AS BIGINT)`, dùng `svi.FIPS` thay `GEOID` | GEOID là VARCHAR, giữ số 0 ở đầu | Bỏ CAST, đổi cột | ~70 |
| K4 | Thiếu bộ lọc miền giá trị | thiếu `FLD_ZONE NOT IN ('X','AREA NOT INCLUDED')`, thiếu `BETWEEN 0 AND 100` cho cột % | Giá trị đặc biệt và miền hợp lệ của từng cột | Thêm điều kiện | ~20 |
| K5 | Sai dạng giá trị chữ | `CITY = 'Houston'` → `'HOUSTON'` | CITY viết hoa | Sửa hằng số | ít |

### Nhóm G — Lỗi SQL chung (ngoài phạm vi chính, để cho baseline xử lý)
Sai hàm gộp, thiếu `CAST AS DOUBLE`/`IS NOT NULL`, nhầm tỉ lệ với phần trăm, sai `LIMIT`/`ORDER BY`, trả về sai cột.
Vẫn cần ghi nhận để báo cáo theo loại lỗi (RQ3). Bộ sửa không cần cố sửa nhóm này.

## 2b. Ghi chú sau khi kiểm tra trên dữ liệu thật (2026-11-02)
- S4 có căn cứ dữ liệu: `hospitals.geometry` là BLOB **rỗng** (1526/1526 dòng), dùng trong hàm ST_* sẽ lỗi; `schools` không có cột geometry.
- K3: 5 câu L4 chuẩn nối `svi.FIPS = census_tracts.GEOID` (BIGINT = VARCHAR). Trên dữ liệu này **không sai** vì mã TX/FL/LA
  (48/12/22) không bắt đầu bằng 0 → bộ kiểm tra chỉ cảnh báo. Đột biến K3 kiểu này sẽ KHÔNG làm đổi kết quả → loại khỏi bộ đột biến.
- Bộ kiểm tra KG v0 (`spatialrepair/detection/checks.py`): 0 báo lỗi oan trên 443 SQL chuẩn, bắt 16/16 đột biến thử.

## 2c. Lỗi quan sát được trên Qwen3.5 nhưng KHÔNG đưa vào phạm vi (2026-11-06)
- **Nhân bản dòng khi nối thẳng bảng claims** (claims ở cấp từng claim, 1,3 triệu dòng; nri/cre/svi ở cấp tract):
  36/443 câu SQL của Qwen3.5 nối thẳng claims rồi tính AVG/SUM trên cột của bảng khác -> 34 câu sai (L1: 22, L3: 14),
  khoảng 16% trong 213 câu sai. Không thêm luật vì 13 câu SQL chuẩn cũng nối thẳng claims theo cùng kiểu
  (không phân biệt được "tính theo tract" hay "tính theo claim" nếu không đọc rất kỹ câu hỏi), và nhiều câu còn kèm lỗi công thức (nhóm G).
  -> Ghi vào phần hạn chế / hướng phát triển.

## 3. Việc cần làm tiếp
- [x] Chạy DuckDB kiểm tra 443 SQL chuẩn (432 match + 11 tie) — `scripts/1_chuan_bi/check_gold.py`
- [ ] Dò tie đầy đủ — `scripts/1_chuan_bi/find_ties.py`
- [ ] Viết bộ sinh đột biến trên AST (sqlglot), mỗi loại lỗi một hàm; chỉ giữ đột biến làm ĐỔI kết quả.
- [x] Soạn cấu trúc KG — `docs/kg_design.md`, `spatialrepair/knowledge/kg.py`
- [ ] Ghi chú các câu SQL chuẩn đáng ngờ (từ ngữ câu hỏi không khớp hàm) để không tính oan khi đo regression.
