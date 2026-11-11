# Fire Monitor

Dashboard giám sát bài tập lớn IoT. Stack gồm Mosquitto, FastAPI chạy model LightGBM đã train và Nginx phục vụ dashboard. Backend đọc MQTT và gửi trạng thái đến trình duyệt qua SSE.

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
  "sensor_status": "OK"
}
```

Khi cảm biến lỗi, gửi trạng thái khác `OK`. Backend sẽ bỏ cửa sổ suy luận hiện tại và chờ mẫu mới. Mẫu trùng hoặc đến sai thứ tự bị bỏ qua. `ir_raw` là tuỳ chọn: giá trị ADC tức thời của cảm biến lửa, dashboard hiển thị trực tiếp trên card **FLAME IR**.

### Cảnh báo từ model xuống thiết bị

Backend publish trạng thái model lên `fire/<device_id>/alert` bằng QoS 1, gửi lại mỗi 5 giây làm heartbeat (không retain). Thiết bị subscribe topic này để bật còi và hiển thị mức cảnh báo:

```json
{
  "schema_version": 1,
  "device_id": "node01",
  "health": "OK",
  "risk_level": "FIRE_ALERT",
  "p_fire": 0.98,
  "threshold": 0.70,
  "window": 5,
  "votes": 5,
  "ir_confirmed": false,
  "latched": true,
  "reason": "LightGBM đủ phiếu cảnh báo",
  "timestamp": 1794383496.37
}
```

`risk_level` có thể là `SAFE`, `ATTENTION`, `FIRE_ALERT` hoặc `null` khi đang tích luỹ mẫu; `health` là `OK`, `WARMUP`, `SENSOR_ERROR`, `OFFLINE`. Nếu quá 15 giây không nhận bản tin mới, thiết bị coi backend mất kết nối.

## Chính sách demo

- Backend tạo 13 đặc trưng động đúng thứ tự của `model_13f/metadata.json` và nạp `lightgbm_dynamic_model.joblib` một lần khi khởi động. Đầu vào model gồm 8 chênh lệch sau 1 và 5 mẫu, 4 độ lệch chuẩn trượt 5 mẫu và tỷ lệ PM2.5/PM1.0. Ngưỡng thấp, ngưỡng cao và cửa sổ bỏ phiếu cũng lấy từ metadata. Khoảng gián đoạn lớn hơn 2,5 giây theo uptime, mất số thứ tự, đổi phiên hoặc mất kết nối sẽ bắt đầu lại cửa sổ.
- LightGBM xác nhận cảnh báo khi ít nhất 3 trong đủ 5 điểm đạt 0,70. Flame IR kích hoạt từ 2 giây kết hợp điểm từ 0,30 cũng xác nhận cảnh báo. Một trong hai điều kiện IR hoặc điểm 0,30 tạo mức cần chú ý. Mức an toàn yêu cầu 10 mẫu liên tiếp dưới 0,30 và IR tắt.
- Dashboard giữ tối đa 120 mẫu và 30 sự kiện cho mỗi thiết bị trong RAM. Chốt cảnh báo demo được giữ qua lỗi cảm biến, mất dữ liệu và ESP32 khởi động lại. Khởi động lại backend xóa phiên giám sát và chốt trong RAM. Cảnh báo được hiển thị trên API và dashboard.
- `GET /api/status` trả trạng thái JSON. `GET /api/events` phát trạng thái thay đổi qua SSE.

```bash
docker compose logs -f backend
docker compose down
```
