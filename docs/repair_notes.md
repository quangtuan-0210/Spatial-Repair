# Bộ sửa v1 (2026-11-03)

`spatialrepair/repair/` — `repair(sql, question, kg)`:
1. Phát hiện lỗi (`detect` = KG + đối chiếu câu hỏi). Không có lỗi mức error -> giữ nguyên.
2. Tạo ứng viên: mỗi mã lỗi có phép sửa cục bộ trên AST (FIXERS: S1-S6, S8, S9, K1-K5).
   S7 không có phép sửa (không phát hiện được — xem detection_notes.md).
3. Tìm kiếm chùm (beam=4, sâu tối đa 4): mỗi bước sửa 1 lỗi, chỉ nhận ứng viên làm giảm lỗi.
4. Chọn ứng viên HẾT lỗi với ít phép sửa nhất; (tuỳ chọn executor) phải chạy được trên DuckDB.
5. Không có ứng viên hết lỗi -> TỪ CHỐI, trả SQL gốc.
Không gọi LLM -> 0 token. Thời gian sửa trung bình ~ vài chục ms / câu (chưa tính chạy DuckDB).

`spatialrepair.repair.canonical()` — dạng chuẩn để so 2 câu: ST_Within(a,b)=ST_Contains(b,a), vị từ đối xứng sắp xếp đối số.

## Kết quả sơ bộ (chưa chạy DuckDB cho 15 câu 'cần chạy'), nửa TEST
- Repair success 558/613 = 91.0% (dev 93.0%); sửa sai 0; từ chối 4; không bắt 36 (S7 13, S1 9, S9 7, K4 4, S2 2, S5 1).
- SQL chuẩn: 441/443 giữ nguyên; 2 câu bị sửa (L4_0007, L4_0008 — đều là SQL chuẩn nghi sai do đếm trùng).

## Điều chỉnh trên dev
- K1: Finding tổng hợp (STATEFP+COUNTYFP) không trỏ nút thật -> tìm literal COUNTYFP để sửa.
- K2: thêm phép sửa LEFT(col, n) sai độ dài trong phép nối.
- Vòng tìm kiếm thử lần lượt các lỗi cho tới khi có phép sửa áp dụng được.

## Kết quả chính thức (đã chạy DuckDB, 2026-11-03) — nửa TEST, 613 đột biến
| Cấu hình | Sửa đúng | Không bắt | Từ chối | Sửa sai |
|---|---|---|---|---|
| KG quan hệ | 0/613 (0.0%) | 590 | 23 | 0 |
| KG không gian | 256/613 (41.8%) | 253 | 104 | 0 |
| KG không gian + câu hỏi | **568/613 (92.7%)** (dev 94.3%) | 36 | 4 | 5 |

- 5 "sửa sai": 3 thuộc L4_0007 (SQL chuẩn nghi sai — bộ sửa thêm DISTINCT đúng) + 2 S1 "overlap" (L5_0011, L5_0012:
  câu hỏi nói overlap, bộ sửa chọn ST_Intersects, SQL chuẩn dùng ST_Overlaps — benchmark dùng lẫn cả hai).
  Bỏ 3 đột biến có SQL chuẩn nghi sai: 568/610 = 93.1%.
- SQL chuẩn: thay đổi 2/443 câu (L4_0007, L4_0008) — đều là SQL chuẩn đếm trùng đã xác nhận bằng audit_gold.py -> regression thật = 0.
- Theo mã (test): 100% ở K3, K5, S3, S4, S6, S8; K1 98.5%; K2 98.3%; S2 96.4%; S1 86.4%; S5 83.3%; S9 82.5%; K4 0%; S7 0%.
- Thời gian sửa: ~20 ms/câu (không tính chạy DuckDB); 0 token.

## Bổ sung B / C / D (sau lần chạy Qwen3.5 đầu tiên)
- **B – cột/bí danh không tồn tại (mã G, phép sửa `fix_G`)**, dựa trên lược đồ trong KG:
  bí danh lạ là tên bảng đã có bí danh khác (`nri.X` khi FROM `nri n`) -> đổi sang `n`;
  `STATEFP` trên bảng không có cột này (nri/cre/svi) -> `LEFT(GEOID, 2)` (giữ tên cột đầu ra nếu nằm trong SELECT);
  cột chỉ có ở đúng một bảng khác trong câu -> gắn lại bí danh (`c.POPUNI` -> `cr.POPUNI`);
  tên gõ sai gần giống (>=0.85) -> đổi tên (`M_DISAB` -> `M_DISABL`). Không có ứng viên -> từ chối.
  Thêm luật phát hiện: cột không bí danh mà không bảng nào trong câu có (bỏ qua câu có CTE/subquery).
- **C – S9 cho nhiều cách hỏi đếm**: ngoài "how many" còn "number of / count of / the most / fewest / largest number of".
  Không mở rộng sang COUNT(*) trong ORDER BY vì chính SQL chuẩn (L4_0002, L4_0018...) cũng làm vậy -> sẽ báo oan.
- **D – hình học hỏng (S7)**: `ST_Intersection/ST_Union/ST_Difference` trên floodplain (3225 hình hỏng) chưa lọc
  `ST_IsValid` chỉ là *nghi vấn* (4 SQL chuẩn cũng làm vậy mà chạy được). Chỉ khi chạy thử ném `TopologyException`
  thì nghi vấn mới thành lỗi và được sửa bằng `WHERE ST_IsValid(<cột hình>)`.
- Kiểm tra: báo oan trên 443 SQL chuẩn không tăng; test 16/16; không câu nào đang đúng bị thay đổi.
