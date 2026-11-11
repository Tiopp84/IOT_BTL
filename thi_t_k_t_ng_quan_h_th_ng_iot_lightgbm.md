# Thiết kế suy luận LightGBM với 13 đặc trưng động

Cập nhật ngày 11/11/2026. Backend nạp `model_13f/lightgbm_dynamic_model.joblib` khi khởi động. Tên cột, thứ tự đầu vào và cấu hình bỏ phiếu lấy từ metadata cùng thư mục. Cấu hình này dùng bốn số đo môi trường có sẵn trong payload ESP32. [Metadata](model_13f/metadata.json) · [Mã suy luận](backend/inference.py)

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

Backend đưa một hàng 13 cột vào `predict_proba` và lấy điểm của lớp `Fire Alarm = 1`. `p_fire` là điểm lớp dương của LightGBM. Tầng luật sử dụng điểm đó, số phiếu và cờ Flame IR để tạo `risk_level`. Giá trị IR được xác nhận khi `ir_active` đúng và `ir_active_duration_ms` từ 2000 ms trở lên. ADC tùy chọn `ir_raw` được hiển thị trên dashboard.

| Tham số | Giá trị chạy |
|---|---:|
| Ngưỡng thấp `T_LOW` | 0,30 |
| Ngưỡng cao `T_HIGH` | 0,70 |
| Kích thước cửa sổ điểm | 5 |
| Số phiếu tối thiểu | 3 |
| Số mẫu điểm thấp liên tiếp để xác nhận `SAFE` | 10 |

Áp dụng theo thứ tự ưu tiên:

1. Giữ `FIRE_ALERT` nếu node đã chốt cảnh báo.
2. Chốt `FIRE_ALERT` nếu đủ 5 điểm và ít nhất 3 điểm đạt 0,70, hoặc Flame IR đã xác nhận và điểm hiện tại đạt 0,30. Nhánh IR kết hợp điểm có thể kích hoạt trong giai đoạn `WARMUP`.
3. Đặt `ATTENTION` nếu điểm hiện tại đạt 0,30 hoặc Flame IR đã xác nhận.
4. Đặt `SAFE` sau 10 mẫu hợp lệ liên tiếp dưới 0,30, với `ir_active` tắt trong cả chuỗi.
5. Giữ mức rủi ro `null` trong các trường hợp đang tích lũy hoặc xác nhận mức an toàn.

`health` mô tả tình trạng dữ liệu riêng với `risk_level`. `WARMUP` nghĩa là cửa sổ có dưới 5 điểm, `OK` nghĩa là đủ điểm, `SENSOR_ERROR` nghĩa là mẫu báo lỗi cảm biến và `OFFLINE` nghĩa là mất MQTT hoặc quá thời hạn nhận mẫu. Một node có thể có `health: OFFLINE` đồng thời giữ `risk_level: FIRE_ALERT` do chốt đang bật. [Luật quyết định trong backend](backend/monitor.py)

## Huấn luyện và xuất mô hình

Notebook `iot-btl.ipynb` dùng Smoke Detection IoT Dataset, đọc 62.630 dòng theo output đã lưu. Notebook sắp xếp theo `UTC`, `CNT` và tách phiên khi khoảng nhảy UTC lớn hơn 10 giây. Trong mỗi phiên, notebook chia theo thời gian thành 60% train, 20% validation và 20% test. Đặc trưng được tính riêng theo phiên bên trong từng tập. Số mẫu train, validation và test lần lượt là 37.576, 12.522 và 12.532. Nhãn là cột `Fire Alarm`.

LightGBM dùng 150 cây, độ sâu tối đa 6, tối đa 31 lá mỗi cây, tốc độ học 0,05 và `random_state=42`. Notebook fit trên train, dùng `predict` để tính F1, precision và recall trên test, dùng `predict_proba` để tính ROC AUC. Ngưỡng vận hành 0,30 và 0,70 được ghi cố định khi xuất metadata. Phép đánh giá bỏ phiếu minh họa trong notebook dùng nhãn dự đoán tức thời với cửa sổ cho phép chưa đủ 5 mẫu. Backend dùng ngưỡng 0,70 trên điểm lớp dương và yêu cầu đủ cửa sổ. [Notebook huấn luyện](iot-btl.ipynb) · [Kết quả và giới hạn đánh giá](Lich_su_phat_trien_va_huan_luyen.md)

Chạy các cell theo thứ tự để xuất `models/lightgbm_dynamic_model.joblib` và `models/metadata.json`. Đặt hai file xuất cùng nhau trong `Src/model_13f` rồi chạy `docker compose up -d --build --wait` từ `Src`. Backend kiểm tra thứ tự và số lượng đặc trưng khi nạp bundle. [Dockerfile](Dockerfile) · [Kiến trúc và MQTT](Thiet_ke_he_thong_IoT_canh_bao_chay.md)
