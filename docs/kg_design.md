# Thiết kế đồ thị tri thức (KG) cho SpatialRepair — v0

## 0. Nguyên tắc
1. **Không dùng SQL chuẩn để dựng KG.** KG chỉ lấy từ: (a) `metadata_parquet.json`, (b) dữ liệu thật trong DuckDB
   (kiểu cột, CRS trong GeoParquet, giá trị của cột phân loại, bảng mã quận), (c) tài liệu ý nghĩa hàm DuckDB Spatial.
   Lý do: nếu học "quy ước" từ SQL chuẩn rồi đem đi sửa chính các câu đó thì là **rò rỉ đáp án** (leakage).
   SQL chuẩn chỉ được dùng để *kiểm tra* KG không báo lỗi oan (mục 5).
2. Mỗi thông tin trong KG phải phục vụ ít nhất 1 loại lỗi trong `analysis/error_taxonomy.md` (S1–S9, K1–K5).
3. Có 2 biến thể để trả lời RQ2: **KG quan hệ** (chỉ lược đồ) và **KG không gian đầy đủ**.

## 1. Các loại nút

| Loại nút | Ví dụ id | Thuộc tính chính | Nguồn | Phục vụ lỗi |
|---|---|---|---|---|
| `Table` | `table:county` | số dòng, cấp (tract/county/zcta/facility/claim/hazard), mô tả | metadata | tất cả |
| `Column` | `col:county.GEOID` | kiểu, null, mô tả, **vai trò** (xem dưới) | metadata + DB | S4, K1–K5, G |
| `GeometrySource` | `geom:hospitals` | loại hình học (POINT/POLYGON), **biểu thức hợp lệ** (`geometry` hoặc `ST_Point(LON, LAT)`), biểu thức **không hợp lệ** (`hospitals.geometry` rỗng) | metadata + DB | S3, S4 |
| `CRS` | `crs:EPSG:4326` | loại (địa lý), đơn vị (độ), trục (x=kinh độ, y=vĩ độ) | GeoParquet + metadata | S3, S6 |
| `SpatialFunction` | `fn:ST_Contains` | nhóm (vị từ/đo/dựng/kiểm tra), kiểu đối số, có đối xứng không, hàm nghịch đảo, đơn vị kết quả | tài liệu DuckDB + metadata | S1, S2, S5, S6 |
| `GeoCode` | `code:state:48`, `code:county:48201` | tên, độ dài mã, cha (bang → quận → tract) | metadata + bảng county | K1, K2 |
| `CodeSystem` | `codesys:tract_geoid` | cấu trúc (2+3+6 chữ số), kiểu VARCHAR, giữ số 0 đầu | metadata | K2, K3 |
| `ValueDomain` | `dom:floodplain.FLD_ZONE` | giá trị hợp lệ, giá trị "đặc biệt" (X, AREA NOT INCLUDED), khoảng [0,100], viết hoa | metadata + DB | K4, K5 |
| `Rule` | `rule:crs_preserve` | mô tả, phạm vi áp dụng, mức (bắt buộc/khuyến nghị) | metadata `_global` | S6, S7, S9, G |

**Vai trò của cột** (suy ra tự động): `geo_key` (GEOID, COUNTYFIPS, ZIP), `geo_code_part` (STATEFP, COUNTYFP),
`numeric_geo_code` (svi.FIPS, svi.ST, cre.STATE/COUNTY/TRACT — kiểu số, **không** dùng để nối), `geometry`,
`geometry_unusable`, `coord_x` (LON), `coord_y` (LAT), `percentage` (EP_*, MP_*, *_PE), `percentile` (EPL_*, RPL_*),
`money` (DECIMAL), `date`, `category`, `name`, `measure`.

## 2. Các loại cạnh

| Cạnh | Từ → Đến | Ý nghĩa | Phục vụ |
|---|---|---|---|
| `HAS_COLUMN` | Table → Column | bảng có cột | S4, G |
| `HAS_GEOMETRY` | Table → GeometrySource | cách lấy hình học của bảng | S3, S4 |
| `IN_CRS` | GeometrySource → CRS | hình học dùng CRS nào | S6 |
| `KEY_JOIN` | Column → Column | nối theo khóa, kèm biến đổi (`=` hoặc `LEFT(.,5)`) | S8, K2, K3 |
| `SPATIAL_JOIN` | Table → Table | nối không gian hợp lệ, kèm kiểu (điểm–đa giác / đa giác–đa giác) và các vị từ phù hợp | S1, S2, S8 |
| `ACCEPTS` | SpatialFunction → loại hình học | đối số thứ i nhận POINT/POLYGON | S2 |
| `INVERSE_OF` | fn:ST_Contains ↔ fn:ST_Within | đổi chỗ đối số thì tương đương | S2 |
| `CODED_BY` | Column → CodeSystem | cột theo hệ mã nào | K2, K3 |
| `PART_OF` | GeoCode → GeoCode | quận thuộc bang | K1 |
| `HAS_DOMAIN` | Column → ValueDomain | miền giá trị | K4, K5 |
| `APPLIES_TO` | Rule → Table/Column/Function | luật áp dụng ở đâu | S6, S7, S9 |

## 3. Hai biến thể (cho RQ2)

| Thành phần | KG quan hệ | KG không gian đầy đủ |
|---|---|---|
| Table, Column (kiểu, mô tả) | ✔ | ✔ |
| KEY_JOIN trực tiếp (`=` cùng tên khóa) | ✔ | ✔ |
| Vai trò cột `geometry`/`coord_x`/`coord_y`, GeometrySource, CRS | ✘ | ✔ |
| SpatialFunction, ACCEPTS, INVERSE_OF, SPATIAL_JOIN | ✘ | ✔ |
| CodeSystem, GeoCode, KEY_JOIN qua `LEFT(.,5)` (khóa địa lý) | ✘ | ✔ |
| ValueDomain, Rule | ✘ | ✔ |

## 4. Cách dựng
`python scripts/2_tri_thuc/build_kg.py` → `kg/kg_spatial.json`, `kg/kg_relational.json`.
- Bước A (không cần DB): đọc metadata → nút/cạnh cơ bản.
- Bước B (`--probe-db`, cần DuckDB): kiểm tra kiểu thật của cột, CRS trong GeoParquet, loại hình học, cột geometry rỗng,
  giá trị của cột phân loại (≤ 60 giá trị), bảng mã 385 quận.
Định dạng: JSON `{"meta", "nodes": [...], "edges": [...]}`. Lớp `spatialrepair.kg.KG` cung cấp hàm tra cứu cho bộ phát hiện/sửa lỗi.

## 5. Kiểm tra KG
- Mọi câu SQL chuẩn phải **qua** các kiểm tra dựa trên KG (không báo lỗi oan). Câu nào bị báo → hoặc KG thiếu, hoặc SQL chuẩn đáng ngờ.
- Sau này: chia dev/test để chỉnh luật trên dev, báo cáo trên test.

## 6. Những điểm đã xác nhận từ dữ liệu thật (2026-11-02)
- Cột geometry của census_tracts, county, floodplain, zcta: kiểu `GEOMETRY`, GeoParquet ghi CRS **WGS 84 địa lý** → đơn vị độ.
- `hospitals.geometry`: BLOB **rỗng** (1526/1526 dòng dài 0) → phải dùng `ST_Point(LON, LAT)`. `schools` không có cột geometry.
- `county`: 380 POLYGON + 5 MULTIPOLYGON; khung tọa độ x ∈ [-106.6, -80.0], y ∈ [24.4, 36.5].
- Metadata mô tả `ST_Area` là "square meters" — **sai** với CRS thật; KG ghi đơn vị = độ².

## 7. Kết quả probe trên toàn bộ dữ liệu (duckdb 2.0, 2026-11-02)
| Bảng | Kiểu cột | Loại hình học | Không hợp lệ | Khung tọa độ (x: kinh độ, y: vĩ độ) |
|---|---|---|---|---|
| census_tracts | GEOMETRY | 13436 POLYGON, 8 MULTI | 0 | x [-106.6, -80.0], y [24.4, 36.5] |
| county | GEOMETRY('EPSG:4326') | 380 POLYGON, 5 MULTI | 0 | như trên |
| zcta | GEOMETRY | 5057 POLYGON, 227 MULTI | 0 | x [-109.1, -80.0], y [24.5, 37.0] |
| floodplain | GEOMETRY | 40568 POLYGON, 875192 MULTI | **3225** | x [-106.4, -80.0], y [24.5, 35.6] |
| claims | GEOMETRY('EPSG:4326') | 1316689 POINT | 0 | x [-170.3, -65.9], y [18.0, 57.1] |
| hospitals | BLOB | — (rỗng 1526/1526) | — | — |

Nhận xét:
- 3 bảng (census_tracts, zcta, floodplain) không gắn CRS trong kiểu cột, nhưng khung tọa độ đều nằm trong khoảng kinh/vĩ độ
  của TX–FL–LA → cùng hệ địa lý với county. Kết luận đơn vị = độ / độ² vẫn đúng.
- **floodplain có 3225 hình không hợp lệ (0,35%)** → `ST_IsValid` thực sự làm thay đổi kết quả với câu dùng floodplain.
  Lỗi S7 (thiếu ST_IsValid) vì vậy có thể là lỗi thật, không chỉ là cảnh báo → cần kiểm chứng bằng đột biến.
- **claims có hình học điểm thật**, nhưng khung tọa độ trải khắp nước Mỹ (cả Alaska, Puerto Rico) dù GEOID chỉ thuộc 3 bang
  → điểm của claims không đáng tin để nối không gian; benchmark đúng khi chỉ nối claims bằng GEOID.
  KG đánh dấu `role = geometry_aux` (có hình học nhưng không nằm trong luật nối không gian).
- `FLD_ZONE` có 10 giá trị: A, AE, AH, AO, AREA NOT INCLUDED, D, OPEN WATER, V, VE, X.
