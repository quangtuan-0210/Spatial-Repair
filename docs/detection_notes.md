# Bộ phát hiện lỗi v1 — KG + đối chiếu câu hỏi (2026-11-03)

## Thành phần
- `spatialrepair/detection/checks.py` — luật dựa trên KG (lược đồ, hình học, CRS, hệ mã, miền giá trị, giá trị rác).
- `spatialrepair/detection/intent.py` — trích ý câu hỏi (mã bang/quận, loại quan hệ, chiều chứa, đếm cái gì, diện tích giao/cả hình,
  giá trị bị loại trừ) rồi so với SQL. `detect()` = checks + intent.
- `spatialrepair/core/split.py` — chia dev/test cố định (băm id). Chỉ chỉnh luật bằng dev; test chỉ để báo cáo.
- `scripts/4_danh_gia/eval_detection.py` — đo báo oan (gold) và độ phủ (đột biến). `scripts/1_chuan_bi/audit_gold.py` — kiểm tra SQL chuẩn nghi sai.

## Kết quả (1247 đột biến L0–L3 + một phần L4/L5)
| Cấu hình | Bắt đúng mã | Báo oan gold dev / test |
|---|---|---|
| KG quan hệ | 4.1% | 0/237 / 0/206 |
| KG không gian | 60.1% | 0/237 / 0/206 |
| KG không gian + câu hỏi | 94.2% | 1/237 / 3/206 |

Theo mã: K1 100%, K2 100%, K3 100%, K5 100%, S3 100%, S4 100%, S6 100%, S8 100%, S2 95.8%, S1 87.2%, S9 84.6%,
S5 75% (n=8), K4 46.2%, **S7 0%**.

## Điều chỉnh luật trên dev (ghi lại để minh bạch)
- Điểm–đa giác: intersects ≈ contains ≈ within (chỉ khác ở biên) → không coi là mâu thuẫn.
- Đa giác–đa giác: chỉ báo **error** khi hàm SQL bị MỌI quan hệ trong câu hỏi loại trừ (vd "within" vs touches);
  mâu thuẫn lỏng ("within floodplain regions" + intersects) chỉ **warn**.
- "contained within/in/by" là bị động = within (lỗi ngữ pháp chung của bộ trích, phát hiện khi đọc 1 câu hỏi test; không xem SQL test).
- Phần trong ngoặc của câu hỏi được dùng khi tìm "not just / entire" (diện tích cả hình).

## Hạn chế / phát hiện về benchmark
- **S7 (bỏ ST_IsValid) = 0%:** câu hỏi không nhắc "valid", SQL chuẩn lúc lọc lúc không → không có căn cứ phát hiện. Ghi là hạn chế.
- **Giá trị rác -999:** 74/80 cột % (svi, cre, nri) có -999 = thiếu dữ liệu. Metadata yêu cầu lọc, nhưng ≥ 9 câu chuẩn (dev)
  chỉ lọc IS NOT NULL → luật để mức **warn**; `scripts/1_chuan_bi/audit_gold.py` cho biết câu nào bị ảnh hưởng thật.
- **Đếm trùng:** L4_0008 (dev) "how many census tracts ... intersect floodplain" nhưng SQL chuẩn COUNT(*) → nghi sai; L4_0007 (test) cùng dạng.
- Báo lỗi trên gold test: L2_0046, L2_0056, L4_0007 — chưa xem SQL (giữ nguyên test), sẽ kiểm bằng audit_gold.py.
