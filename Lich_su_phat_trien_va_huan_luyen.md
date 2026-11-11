# Lịch sử phát triển và huấn luyện mô hình cảnh báo cháy

Tổng hợp đến 11/11/2026 từ notebook, bundle mô hình và mã nguồn. Bản đang triển khai là LightGBM 13 đặc trưng động. Các chỉ số dưới đây được ghi theo đúng cách đánh giá của từng phiên bản để có thể trích vào báo cáo.

## Các giai đoạn phát triển

| Giai đoạn | Thay đổi và mục đích | Dấu vết trong dự án |
|---|---|---|
| Kiến trúc ban đầu | ESP32 gửi dữ liệu qua Mosquitto. Node-RED dùng để trực quan hóa trong stack cũ. Random Forest được xây dựng làm đường cơ sở với bốn số đo tức thời. | [Compose ban đầu](../Server/docker-compose.yml) · [Notebook so sánh](<iot-btl (1).ipynb>) |
| LightGBM 16 đặc trưng | Kết hợp số đo thô, hai tỷ lệ, chênh lệch sau 5 mẫu, trung bình trượt và độ lệch chuẩn trượt. Thử cửa sổ bỏ phiếu để giảm cảnh báo do một mẫu điểm cao. | [Notebook 16 đặc trưng](<iot-btl (1).ipynb>) · [Metadata 16 đặc trưng](smoke_model_bundle/metadata.json) |
| Backend và dashboard riêng | Đưa suy luận vào FastAPI, dùng Nginx phục vụ dashboard và SSE để cập nhật. Lượt kiểm tra đầu tiên dùng CSV phát qua MQTT. Sau đó bỏ replay CSV khỏi stack và thêm hai kịch bản giả lập 1 Hz chạy trong phiên riêng. | [Docker Compose](docker-compose.yml) · [Luồng MQTT và giả lập](backend/app.py) |
| Thử nghiệm đặc trưng động | Khảo sát các nhóm đặc trưng động, đồng thời xem kết quả khi giữ lại cả nhóm phiên để thử trên nhóm chưa dùng lúc train. Notebook khảo sát có các cấu hình `baseline16`, `dynamic7` và `dynamic11`. | [Notebook khảo sát](../Model/lightgbm-dynamic-only.ipynb) |
| LightGBM 13 đặc trưng động | Bỏ mức tuyệt đối của bốn kênh và các trung bình trượt khỏi vector. Thêm chênh lệch sau 1 mẫu và độ lệch chuẩn trượt của hai kênh PM. Giữ chênh lệch 5 mẫu, độ lệch chuẩn nhiệt độ và độ ẩm, cùng tỷ lệ PM2.5/PM1.0. | [Notebook huấn luyện](iot-btl.ipynb) · [Metadata 13 đặc trưng](model_13f/metadata.json) |
| Tích hợp 13 đặc trưng | Đổi bundle trong Docker, cập nhật bộ trích xuất và lấy ngưỡng từ metadata. Dashboard dùng danh sách 13 cột từ backend. ESP32 tiếp tục gửi payload hiện có và backend phát kết quả qua `fire/<device_id>/alert`. | [Mã suy luận](backend/inference.py) · [Luồng cảnh báo](backend/app.py) |

## Cơ sở lựa chọn mô hình

**Random Forest** tạo mốc so sánh với bốn số đo tức thời, gồm nhiệt độ, độ ẩm, PM1.0 và PM2.5. Notebook dùng 100 cây, độ sâu tối đa 12 và seed 42. Mốc này giúp xem giá trị bổ sung của đặc trưng chuỗi thời gian.

**LightGBM 16 đặc trưng** biểu diễn cả mức hiện tại lẫn biến động. Bản xuất đã chọn ngưỡng cao 0,95 sau khi thử các ngưỡng trên validation cho luật 3/5. FPR validation là 15,98%, cao hơn mục tiêu 10%. Trên test theo khối thời gian trong cùng phiên, FPR của luật 3/5 là 49,13%, so với 49,30% khi cảnh báo trực tiếp ở ngưỡng 0,95. Trong lượt đánh giá đó, bỏ phiếu chỉ giảm nhẹ tỷ lệ dương tính giả.

**LightGBM 13 đặc trưng** là cấu hình được chọn để triển khai theo hướng giảm phụ thuộc vào mức nền tuyệt đối giữa môi trường huấn luyện và phòng đặt cảm biến. Tám chênh lệch đo biến động ngắn hạn, bốn độ lệch chuẩn mô tả mức dao động, còn tỷ lệ PM giữ thông tin tương quan giữa hai kênh bụi. Đây là động cơ thiết kế. Một vector minh họa điều kiện phòng được nhập thủ công trong notebook cho `p_fire = 0,1112`. Kết quả đó mô tả một đầu vào cụ thể và chưa định lượng tỷ lệ báo nhầm trong phòng.

## Quy trình huấn luyện có thể trình bày trong báo cáo

1. **Chuẩn bị dữ liệu.** Dùng Smoke Detection IoT Dataset, với cột `Fire Alarm` làm nhãn nhị phân. Notebook đọc 62.630 bản ghi, sắp xếp theo `UTC` và `CNT`, sau đó nhận diện 5 phiên bằng khoảng nhảy UTC lớn hơn 10 giây.
2. **Chia tập trước khi trích đặc trưng.** Lấy 60% đầu, 20% tiếp theo và 20% cuối trong mỗi phiên làm train, validation và test. Bản 13 đặc trưng có 37.576 mẫu train, 12.522 mẫu validation và 12.532 mẫu test. Tính chênh lệch và thống kê trượt riêng trong từng đoạn của từng phiên để cửa sổ không lấy mẫu từ đoạn trước ở ranh giới chia tập.
3. **Huấn luyện.** Fit `LGBMClassifier` trên train với `n_estimators=150`, `max_depth=6`, `num_leaves=31`, `learning_rate=0.05`, `random_state=42`. Với bản 13 đặc trưng, notebook chấm các chỉ số trên test sau khi fit và ghi các ngưỡng vận hành cố định vào metadata.
4. **Xuất và triển khai.** Xuất `lightgbm_dynamic_model.joblib` và `metadata.json`. Đặt cặp file trong `Src/model_13f`, build lại Docker và xác nhận backend nạp đúng thứ tự 13 cột. Ngưỡng triển khai là 0,30 và 0,70, với ít nhất 3 điểm đạt ngưỡng cao trong đủ 5 mẫu để kích hoạt nhánh cảnh báo của mô hình. [Notebook](iot-btl.ipynb) · [Thiết kế suy luận](thi_t_k_t_ng_quan_h_th_ng_iot_lightgbm.md)

## Kết quả ghi nhận

| Cấu hình và cách chấm | Precision | Recall | F1 | ROC AUC | FPR |
|---|---:|---:|---:|---:|---:|
| Random Forest 4 số đo, `predict` trong notebook so sánh | 0,8483 | 0,9131 | 0,8795 | 0,5184 | — |
| LightGBM 16 đặc trưng, `predict` trong notebook so sánh | 0,8969 | 0,9776 | 0,9355 | 0,7829 | — |
| LightGBM 16 đặc trưng, test tại ngưỡng 0,95 theo từng mẫu | 0,8852 | 0,8548 | 0,8697 | — | 49,30% |
| LightGBM 16 đặc trưng, test với luật 3/5 tại ngưỡng 0,95 | 0,8859 | 0,8572 | 0,8713 | — | 49,13% |
| LightGBM 13 đặc trưng, `predict` trên test của notebook 13 đặc trưng | 0,7944 | 0,6720 | 0,7281 | 0,3029 | — |

F1, precision và recall của hai dòng `predict` đầu lấy từ bảng kết quả notebook so sánh. Hai dòng dùng ngưỡng 0,95 lấy từ bundle 16 đặc trưng. Dòng cuối lấy từ bundle 13 đặc trưng. ROC AUC dùng điểm lớp dương `predict_proba`. Dấu “—” biểu thị chỉ số không được trích cho cách chấm đó. [Notebook so sánh](<iot-btl (1).ipynb>) · [Metadata 16 đặc trưng](smoke_model_bundle/metadata.json) · [Metadata 13 đặc trưng](model_13f/metadata.json)

Khi viết báo cáo, ghi rõ test trong hai notebook huấn luyện chính là **đoạn cuối của những phiên cũng có đoạn train**. Việc giữ thứ tự thời gian giúp giảm việc trộn các mẫu kề nhau giữa các tập, nhưng kết quả vẫn phản ánh test trong cùng nhóm phiên. Thí nghiệm giữ cả nhóm phiên nằm ở notebook khảo sát riêng và dùng các bộ đặc trưng khác. Kết quả 13 đặc trưng có ROC AUC 0,3029, dưới mốc 0,5 của xếp hạng ngẫu nhiên, nên dữ liệu hiện có chưa hỗ trợ kết luận rằng bản 13 đặc trưng phân biệt cháy tốt hơn bản 16. Các con số của bản 13 cũng chưa đo hiệu quả của toàn bộ luật hợp nhất Flame IR và chốt cảnh báo.

## Kiểm chứng triển khai và phần thực nghiệm cần ghi riêng

Trong lượt chuyển đổi, backend Docker đã được đối chiếu với hàm trích đặc trưng của notebook trên **66 mẫu kiểm tra**. Cả 13 giá trị đầu vào và điểm mô hình khớp trong sai số kiểm tra. API, kết nối broker, luồng giả lập và tài nguyên dashboard đã qua kiểm tra. Kết quả này xác nhận tính nhất quán giữa notebook và backend trên các mẫu đã kiểm tra.

Khi có số liệu thực nghiệm, ghi riêng mã node, phiên đo, điều kiện môi trường, nhịp gửi, thời gian kích hoạt, mức cảnh báo, bản tin MQTT và trạng thái LED/còi quan sát được. Các chỉ số cấp hệ thống như số lần báo nhầm, số tình huống bỏ sót và độ trễ đầu cuối cần gắn với tập phiên đo và tiêu chí xác định sự kiện. [Thiết kế hệ thống và quy trình thử](Thiet_ke_he_thong_IoT_canh_bao_chay.md)
