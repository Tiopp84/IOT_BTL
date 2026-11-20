# Thiết kế suy luận LightGBM với 13 đặc trưng động

Cập nhật ngày 20/11/2026. Backend nạp `lightgbm_clean13_field_v2_bundle/lightgbm_clean13_field_v2.joblib` khi khởi động. Tên cột, thứ tự đầu vào và ngưỡng phân lớp lấy từ metadata cùng thư mục. Luật bỏ phiếu và ngưỡng chú ý nằm trong `backend/decision_policy.json`. Cấu hình dùng bốn số đo môi trường từ payload ESP32. [Metadata](lightgbm_clean13_field_v2_bundle/metadata.json) · [Mã suy luận](backend/inference.py)

## Tạo đặc trưng trực tuyến

Gọi `x[t]` là số đo tại mẫu hiện tại. Một node có bộ đệm tối đa 6 mẫu để tính `x[t] - x[t-5]`. Độ lệch chuẩn dùng tối đa 5 mẫu gần nhất, gồm mẫu hiện tại, với mẫu số `n - 1` như `pandas.rolling(...).std()` trong notebook. Chênh lệch thiếu mẫu tham chiếu và độ lệch chuẩn chỉ có một mẫu được điền 0. Giá trị PM1.0 và PM2.5 tức thời đi vào tỷ lệ sau khi cộng `0.001` cho cả tử và mẫu.

| Vị trí | Tên đặc trưng theo metadata | Công thức |
|---:|---|---|
| 1–2 | `d_temp_1`, `d_temp_5` | Chênh lệch nhiệt độ sau 1 và 5 mẫu |
| 3–4 | `d_hum_1`, `d_hum_5` | Chênh lệch độ ẩm sau 1 và 5 mẫu |
| 5–6 | `d_pm1_1`, `d_pm1_5` | Chênh lệch PM1.0 sau 1 và 5 mẫu |
| 7–8 | `d_pm25_1`, `d_pm25_5` | Chênh lệch PM2.5 sau 1 và 5 mẫu |
| 9 | `temp_roll_5_std` | Độ lệch chuẩn trượt của nhiệt độ |
| 10 | `hum_roll_5_std` | Độ lệch chuẩn trượt của độ ẩm |
| 11 | `pm1_roll_5_std` | Độ lệch chuẩn trượt của PM1.0 |
| 12 | `pm25_roll_5_std` | Độ lệch chuẩn trượt của PM2.5 |
| 13 | `pm_ratio` | `(PM2.5 + 0.001) / (PM1.0 + 0.001)` |

ESP32 gửi ở nhịp danh định 1 Hz, nên chênh lệch 5 mẫu tương ứng khoảng 5 giây khi luồng đều. Backend bắt đầu lại cửa sổ khi đổi phiên, thiếu số thứ tự, khoảng cách uptime trên 2,5 giây, gián đoạn nhận từ 5 giây, mất kết nối broker hoặc có mẫu báo lỗi cảm biến. Bộ đệm đặc trưng và bộ đệm bỏ phiếu có kích thước riêng. [Bộ đệm và điều kiện gián đoạn](backend/monitor.py)

## Điểm mô hình và luật cảnh báo

Backend chờ đủ sáu mẫu liên tục rồi đưa một hàng 13 cột vào `predict_proba` và lấy điểm của lớp khởi phát `training_label = 1`. Trường `p_fire` mang điểm này để các thiết bị hiện có đọc được. API trả `model.score_kind = onset`, tên model và SHA-256 bundle. `clean13` không dùng hai đặc trưng baseline tương đối.

Tầng luật sử dụng điểm, số phiếu và cờ Flame IR để tạo `risk_level`. IR được xác nhận khi `ir_active` đúng và `ir_active_duration_ms` từ 2000 ms trở lên. ADC tuỳ chọn `ir_raw` được hiển thị trên dashboard.

| Tham số | Giá trị chạy |
|---|---:|
| Ngưỡng thấp `T_LOW`, cấu hình server | 0,10 |
| Ngưỡng cao `T_HIGH`, từ metadata | 0,25 |
| Kích thước cửa sổ điểm | 5 |
| Số phiếu tối thiểu | 3 |
| Số mẫu đạt ngưỡng thấp liên tiếp để xác nhận `ATTENTION` từ model | 3 |
| Số mẫu điểm thấp liên tiếp để xác nhận `SAFE` | 10 |

Áp dụng theo thứ tự ưu tiên:

1. Đặt `FIRE_ALERT` nếu đủ 5 điểm hợp lệ và ít nhất 3 điểm đạt 0,25, hoặc Flame IR đã xác nhận và điểm hiện tại đạt 0,10. Nhánh IR kết hợp điểm chỉ dùng sau khi đã có đủ sáu mẫu tính đặc trưng.
2. Đặt `ATTENTION` nếu 3 điểm liên tiếp đạt 0,10 hoặc Flame IR đã xác nhận.
3. Đặt `SAFE` sau 10 điểm liên tiếp dưới 0,10, với `ir_active` tắt trong cả chuỗi. Dashboard diễn giải là điểm khởi phát thấp.
4. Giữ mức rủi ro `null` khi đang tích luỹ lịch sử hoặc điểm.

`health` mô tả tình trạng dữ liệu riêng với `risk_level`. `WARMUP` gồm thời gian chờ sáu mẫu cảm biến và chờ đủ năm điểm, `OK` nghĩa là đủ điểm, `SENSOR_ERROR` nghĩa là mẫu báo lỗi cảm biến và `OFFLINE` nghĩa là mất MQTT hoặc quá thời hạn nhận mẫu. Ở 1 Hz, cửa sổ bỏ phiếu đầu tiên đủ tại mẫu thứ 10. Điểm thấp của model khởi phát chưa xác nhận sự kiện đã kết thúc. Dashboard dùng `measured_risk_level` và `measured_reason` theo từng mẫu. [Luật quyết định](backend/monitor.py)

## Huấn luyện và xuất mô hình

Bundle v2 dùng nhãn khởi phát suy ra từ đường cảm biến. Chỉ nền được chọn và các cửa sổ khởi phát tham gia học, các đoạn tiếp diễn/hồi phục bị loại khỏi loss. Phần dữ liệu gốc sạch có 14.864 hàng, gồm 832 mẫu dương; model field_clean13 học thêm 1.811 mẫu âm thực nghiệm. Phiên room_20261112 dùng chọn ngưỡng, room_20261120 dùng đánh giá, khói giấy chưa gán nhãn không tham gia học. Validation giữ các cặp phiên gần trùng trong nhóm A/B/C. [Notebook](train_lightgbm_transient_clean_hybrid_kaggle_v2.ipynb) · [Metadata](lightgbm_clean13_field_v2_bundle/metadata.json)

LightGBM dùng 150 cây, độ sâu tối đa 6, tối đa 31 lá, learning rate 0,05 và random_state=42. Notebook v2 so sánh source_clean13/field_clean13/field_augmented13 và quét ngưỡng 0,05–0,25. Model xuất là clean13, ngưỡng 0,25. Ngưỡng chú ý 0,10 do người tích hợp chọn cho server. Các chỉ số trong bundle chấm điểm/phân lớp tức thời, hiệu năng luật 3/5 cần đo riêng. [Kết quả bundle](lightgbm_clean13_field_v2_bundle/results.json)

Đặt `lightgbm_clean13_field_v2.joblib` và `metadata.json` cùng trong `Src/lightgbm_clean13_field_v2_bundle`, rồi chạy `docker compose up -d --build --wait` từ `Src`. Backend kiểm tra variant clean13, thứ tự cột và lớp dương khi khởi động. [Dockerfile](Dockerfile) · [Kiến trúc và MQTT](Thiet_ke_he_thong_IoT_canh_bao_chay.md)
