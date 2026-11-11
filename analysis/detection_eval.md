# Độ chính xác phát hiện lỗi — tập có nhãn (all)

Tập có nhãn: **443 SQL chuẩn** (không lỗi) + **1355 SQL lỗi** (đột biến, biết mã). Chỉ tính phát hiện mức `error`.

## (1) Có lỗi / không lỗi

| Cấu hình | TP | FN | FP (báo oan) | TN | Precision | Recall | F1 | Accuracy | Báo oan % | Đúng mã % |
|---|---|---|---|---|---|---|---|---|---|---|
| kg_relational | 52 | 1303 | 0 | 443 | 100.0 | 3.8 | 7.4 | 27.5 | 0.0 | 3.8 |
| kg_spatial (không đọc câu hỏi) | 810 | 545 | 14 | 429 | 98.3 | 59.8 | 74.3 | 68.9 | 3.2 | 59.5 |
| spatialrepair (KG + câu hỏi) | 1288 | 67 | 18 | 425 | 98.6 | 95.1 | 96.8 | 95.3 | 4.1 | 94.5 |

## (2) Theo mã lỗi — Recall / Precision / F1 (%)

| Mã | n | kg_relational: R / P / F1 | kg_spatial (không đọc câu hỏi): R / P / F1 | spatialrepair (KG + câu hỏi): R / P / F1 |
|---|---|---|---|---|
| K1 | 425 | 0 / - / - | 54 / 100 / 70 | 100 / 100 / 100 |
| K2 | 135 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| K3 | 37 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| K4 | 13 | 0 / - / - | 46 / 15 / 23 | 92 / 24 / 39 |
| K5 | 27 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| S1 | 206 | 0 / - / - | 0 / - / - | 86 / 99 / 92 |
| S2 | 127 | 0 / - / - | 87 / 100 / 93 | 96 / 95 / 95 |
| S3 | 112 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| S4 | 113 | 46 / 100 / 63 | 100 / 100 / 100 | 100 / 100 / 100 |
| S5 | 9 | 0 / - / - | 0 / - / - | 78 / 88 / 82 |
| S6 | 22 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| S7 | 25 | 0 / - / - | 0 / - / - | 0 / - / - |
| S8 | 16 | 0 / - / - | 100 / 100 / 100 | 100 / 100 / 100 |
| S9 | 88 | 0 / - / - | 0 / - / - | 86 / 90 / 88 |

- S7 (thiếu ST_IsValid) không phát hiện được chỉ bằng đọc SQL vì nhiều SQL chuẩn cũng không lọc mà vẫn chạy đúng; SpatialRepair chỉ coi là lỗi khi chạy thử ném TopologyException (bước sửa), nên ở bảng phát hiện tĩnh này recall S7 = 0.
- K4: phần lớn báo oan là SQL chuẩn không loại giá trị rác −999 trước khi gộp (SQL chuẩn có thể chính nó chưa sạch).

Ghi chú: Precision theo mã tính trên toàn bộ tập (cả SQL chuẩn bị báo oan mã đó, và đột biến bị báo thêm mã khác mã thật).
SQL chuẩn bị báo oan (spatialrepair): L0_0015, L1_0038, L1_0039, L1_0040, L1_0043, L1_0075, L2_0046, L2_0056, L3_0001, L3_0007, L3_0017, L3_0027, L3_0031, L3_0032, L3_0038, L3_0049, L4_0007, L4_0008
