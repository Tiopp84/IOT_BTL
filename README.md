# Fire Monitor

Dashboard giám sát bài tập lớn IoT. Stack gồm Mosquitto, FastAPI chạy model LightGBM đã train và Nginx phục vụ dashboard. Backend đọc MQTT và gửi trạng thái đến trình duyệt qua SSE.

Backend hiện nạp bundle `clean13 field v2` trong `lightgbm_clean13_field_v2_bundle`, gồm `lightgbm_clean13_field_v2.joblib` và `metadata.json`. Model dùng 13 đặc trưng động, chấm điểm khởi phát từ mẫu thứ sáu liên tục. `GET /api/status` trả thêm `model` với tên, mục tiêu, ngưỡng và SHA-256 để kiểm tra bundle đang chạy. Dashboard hiển thị tên model và điểm khởi phát.

## Chạy trên máy có Docker Compose

Từ thư mục `BTL/Src`:

```bash
docker compose up -d --build --wait
docker compose ps
```

Mở [dashboard](http://localhost:8080). Từ thiết bị khác trong cùng mạng, dùng địa chỉ IP của máy chạy Docker thay cho `localhost`. ESP32 kết nối MQTT đến IP đó, cổng `1883`. Broker cho phép kết nối ẩn danh để demo trong mạng lab.

Nếu stack cũ trong `BTL/Server` đang dùng cổng 1883, có thể chạy stack mới với cổng khác. Giữ giá trị này cho các lần chạy tiếp theo, ví dụ đặt `MQTT_PORT=1884` trong file `.env` cạnh Compose. Đặt `DASHBOARD_PORT` nếu cần đổi cổng web.

```bash
MQTT_PORT=1884 docker compose up -d --build --wait
```

## Dùng Makefile

Các lệnh thường dùng đã được gói trong `Makefile` (chạy `make help` để xem tất cả):

```bash
make up            # build và khởi động stack, chờ healthy
make ps            # xem trạng thái container
make logs          # theo dõi log toàn stack
make logs SERVICE=backend
make health        # kiểm tra API qua dashboard
make down          # dừng stack
```

Có thể đổi cổng trực tiếp, ví dụ `make up DASHBOARD_PORT=8081 MQTT_PORT=1884`. Giá trị trong file `.env` vẫn được Docker Compose đọc như bình thường.

Cùng `Makefile` cũng chạy được các lệnh Git thường dùng:

```bash
make git-status                    # trạng thái thay đổi
make git-log                       # 15 commit gần nhất (make git-log N=30)
make git-diff                      # diff chưa stage
make git-add FILES=backend/app.py  # stage file cụ thể (mặc định tất cả)
make git-commit MSG="sửa API"      # commit phần đã stage
make git-sync MSG="cập nhật"       # add tất cả + commit + push
make git-pull                      # pull rebase + autostash
```

Trong mục **Nguồn dữ liệu**, chọn **Giả lập 1 Hz · Khói đột ngột** để xem bụi mịn tăng nhanh rồi giảm khi khói tan, hoặc **Giả lập 1 Hz · Nhiệt tăng nhanh** để xem nhiệt độ tăng và độ ẩm tương đối giảm trong khi bụi gần như ổn định. Mỗi kịch bản có 12 giây đo nền trước khi sự kiện xảy ra. LightGBM xử lý từng mẫu trong phiên riêng của trình duyệt. Chọn **Chạy lại** để bắt đầu lại kịch bản hoặc **ESP32 qua MQTT** để xem dữ liệu thiết bị.

## Telemetry từ ESP32

Dashboard hiển thị số đo (kể cả ADC tức thời của cảm biến lửa), 13 đặc trưng động đầu vào và cửa sổ điểm từ backend. Khi chưa nhận bản tin hợp lệ, giao diện hiển thị trạng thái chờ. Khi thiết bị ngừng gửi khoảng 5 giây, dashboard báo mất dữ liệu và giữ biểu đồ lịch sử.

Gửi QoS 1 lên `fire/node01/telemetry`, tắt retain. ID trong topic phải khớp payload. Tạo `boot_id` mới mỗi lần ESP32 khởi động và tăng `seq` mỗi mẫu. Gửi mẫu ở 1 Hz.

```json
{
  "schema_version": 1,
  "device_id": "node01",
  "boot_id": "boot-unique-id",
  "seq": 1,
  "uptime_ms": 1000,
  "temperature_c": 29.5,
  "humidity_pct": 58.4,
  "pm1_ug_m3": 15.2,
  "pm25_ug_m3": 22.0,
  "ir_active": false,
  "ir_active_duration_ms": 0,
  "ir_raw": 3548,
  "buzzer_enabled": true,
  "sensor_status": "OK"
}
```

Khi cảm biến lỗi, gửi trạng thái khác `OK`. Backend sẽ bỏ cửa sổ suy luận hiện tại và chờ mẫu mới. Mẫu trùng hoặc đến sai thứ tự bị bỏ qua. `ir_raw` là tuỳ chọn: giá trị ADC tức thời của cảm biến lửa, dashboard hiển thị trực tiếp trên card **FLAME IR**. `buzzer_enabled` là tuỳ chọn, dùng để đồng bộ trạng thái loa thật lên dashboard.

### Cảnh báo từ model xuống thiết bị

Backend publish trạng thái model lên `fire/<device_id>/alert` bằng QoS 1, gửi lại mỗi 5 giây làm heartbeat (không retain). Thiết bị subscribe topic này để bật còi và hiển thị mức cảnh báo:

```json
{
  "schema_version": 1,
  "device_id": "node01",
  "health": "OK",
  "risk_level": "FIRE_ALERT",
  "p_fire": 0.98,
  "score_kind": "onset",
  "model_name": "LightGBM_Clean13_Field_v2",
  "threshold": 0.25,
  "window": 5,
  "votes": 5,
  "ir_confirmed": false,
  "buzzer_enabled": true,
  "reason": "Điểm khởi phát LightGBM đủ phiếu cảnh báo",
  "timestamp": 1794383496.37
}
```

`risk_level` có thể là `SAFE`, `ATTENTION`, `FIRE_ALERT` hoặc `null` khi đang tích luỹ mẫu. `health` là `OK`, `WARMUP`, `SENSOR_ERROR`, `OFFLINE`. Nếu quá 15 giây không nhận bản tin mới, thiết bị coi backend mất kết nối. Trường `p_fire` mang điểm khởi phát của model mới để firmware và log hiện có tiếp tục đọc được. `SAFE` được hiển thị là điểm khởi phát thấp. Model đã loại giai đoạn tiếp diễn khỏi dữ liệu học, nên điểm thấp chưa xác nhận sự kiện đã kết thúc. Mức cảnh báo hiện được cập nhật theo từng chuỗi điểm.

### Điều khiển loa từ dashboard

Dashboard có nút **Loa · Bật/Tắt**. Khi bấm, trình duyệt gọi `POST /api/devices/<device_id>/buzzer` với body `{"enabled": false}` (hoặc `true`). Backend publish lệnh xuống `fire/<device_id>/cmd` bằng QoS 1:

```json
{ "schema_version": 1, "buzzer_enabled": false }
```

ESP32 subscribe topic `cmd`, tắt/bật còi ngay và gửi lại `buzzer_enabled` trong telemetry 1 Hz để dashboard hiển thị đúng trạng thái thật. Khi bật loa, còi bíp 150 ms để xác nhận; sau đó còi chỉ hú khi đang có cảnh báo. Loa tắt chỉ ngắt còi; màn hình, đèn và cảnh báo trên dashboard vẫn giữ nguyên.

## Chính sách demo

- Backend nạp `lightgbm_clean13_field_v2_bundle/lightgbm_clean13_field_v2.joblib` một lần khi khởi động và kiểm tra đúng thứ tự 13 cột trong metadata. Đầu vào gồm 8 chênh lệch sau 1 và 5 mẫu, 4 độ lệch chuẩn trượt 5 mẫu và tỷ lệ PM. Bundle `clean13` không dùng đặc trưng baseline tương đối.
- Ngưỡng xác nhận 0,25 lấy từ `classification_threshold` trong metadata của bundle. Ngưỡng chú ý 0,10 và luật 3/5 nằm riêng trong `backend/decision_policy.json`. Ngưỡng chú ý là cấu hình vận hành do người tích hợp chọn. Các chỉ số phân lớp trong bundle dùng điểm tức thời; hiệu năng sau luật 3/5 cần đánh giá riêng.
- Chờ đủ 6 mẫu cảm biến liên tục trước điểm đầu tiên, sau đó chờ đủ 5 điểm hợp lệ để áp dụng 3/5. Ở nhịp 1 Hz, nhánh bỏ phiếu sẵn sàng từ mẫu thứ 10. Gián đoạn trên 2,5 giây theo uptime, mất số thứ tự, đổi boot, lỗi cảm biến hoặc mất MQTT sẽ reset lịch sử và phiếu.
- LightGBM xác nhận cảnh báo khi ít nhất 3 trong đủ 5 điểm đạt 0,25. Flame IR kích hoạt từ 2 giây kết hợp điểm hợp lệ từ 0,10 cũng xác nhận cảnh báo. Mức cần chú ý yêu cầu 3 điểm liên tiếp đạt 0,10 hoặc IR đã xác nhận. Sau 10 điểm dưới 0,10 và IR tắt, dashboard hiển thị điểm khởi phát thấp.
- Dashboard giữ tối đa 120 mẫu và 30 sự kiện mỗi thiết bị trong RAM. Firmware nhận `p_fire` như trước, mã nguồn OLED dùng `on=` để biểu thị điểm khởi phát và `THEO DOI` cho mã trạng thái `SAFE`. Cần nạp firmware cập nhật để các chữ này xuất hiện trên board.
- `GET /api/status` trả trạng thái JSON. `GET /api/events` phát trạng thái thay đổi qua SSE. `POST /api/devices/<device_id>/buzzer` bật/tắt còi trên thiết bị.

```bash
docker compose logs -f backend
docker compose down
```

Kiểm tra schema, công thức đặc trưng và vòng khởi tạo bằng `python -m unittest discover -s tests -v` trong môi trường có các thư viện từ `requirements.txt`.

### Bộ lọc PM giảm ở tầng quyết định

Cấu hình `backend/decision_policy.json` bật `pm_trend_gate`, với biên giảm ban đầu 1 µg/m³. Chỉ chặn phiếu mới và chuỗi chú ý từ model khi cả PM1 và PM2.5 đều giảm ít nhất biên này sau 5 khoảng mẫu, đồng thời trung bình 3 mẫu gần nhất giảm ít nhất biên so với 3 mẫu trước. Biên này là cấu hình vận hành ban đầu, chưa được tối ưu bằng validation có nhãn xu hướng.

Điểm model và cửa sổ điểm thô giữ nguyên; API bổ sung `pm_declining`, `onset_vote_eligible` và cấu hình bộ lọc. Phiếu cũ vẫn hết hạn theo cửa sổ 3/5 hiện có, không bị xóa ngay khi PM giảm. Flame IR vẫn xử lý độc lập. PM giảm không tự tạo trạng thái SAFE; server vẫn dùng điều kiện điểm thấp/IR hiện có để kết thúc trạng thái cảnh báo, chưa có cơ chế chốt cảnh báo đến khi xác nhận thủ công.
